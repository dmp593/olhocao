import shutil
from pathlib import Path
from uuid import UUID, uuid4

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from django.views.generic import View
from PIL import Image, UnidentifiedImageError

from .models import BookingStay, BookingStayMedia

ALLOWED_MEDIA_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".png": "image/png",
    ".webm": "video/webm",
    ".webp": "image/webp",
}
MAX_CHUNKS = 200


@method_decorator(csrf_protect, name="dispatch")
class StayMediaUploadChunkView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not request.user.is_staff:
            return HttpResponseForbidden()

        stay = get_object_or_404(BookingStay, pk=pk)
        chunk = request.FILES.get("chunk")
        if chunk is None:
            return JsonResponse({"error": "missing chunk"}, status=400)

        try:
            upload_uuid = UUID(request.POST.get("upload_id", ""))
            chunk_index = int(request.POST.get("chunk_index", ""))
            total_chunks = int(request.POST.get("total_chunks", ""))
            declared_size = int(request.POST.get("size", ""))
        except (TypeError, ValueError):
            return JsonResponse({"error": "invalid upload parameters"}, status=400)

        maximum_size = settings.STAY_MEDIA_MAX_UPLOAD_BYTES
        if not 1 <= total_chunks <= MAX_CHUNKS:
            return JsonResponse({"error": "invalid chunk count"}, status=400)
        if not 0 <= chunk_index < total_chunks:
            return JsonResponse({"error": "invalid chunk index"}, status=400)
        if not 0 < declared_size <= maximum_size or chunk.size > maximum_size:
            return JsonResponse({"error": "file is too large"}, status=400)

        original_filename = Path(request.POST.get("filename", "upload.bin")).name
        extension = Path(original_filename).suffix.lower()
        expected_content_type = ALLOWED_MEDIA_TYPES.get(extension)
        supplied_content_type = request.POST.get("content_type", "")
        if expected_content_type is None or supplied_content_type != expected_content_type:
            return JsonResponse({"error": "unsupported media type"}, status=400)

        upload_root = (
            Path(settings.MEDIA_ROOT) / "tmp" / "stay_uploads" / str(stay.pk) / upload_uuid.hex
        )
        upload_root.mkdir(parents=True, exist_ok=True)
        part_path = upload_root / f"{chunk_index:06d}.part"
        with part_path.open("wb") as destination:
            for data in chunk.chunks():
                destination.write(data)

        received_size = sum(path.stat().st_size for path in upload_root.glob("*.part"))
        if received_size > declared_size or received_size > maximum_size:
            shutil.rmtree(upload_root, ignore_errors=True)
            return JsonResponse({"error": "received data exceeds file size"}, status=400)

        if chunk_index + 1 != total_chunks:
            return JsonResponse({"completed": False})

        part_paths = [upload_root / f"{index:06d}.part" for index in range(total_chunks)]
        if not all(path.is_file() for path in part_paths):
            return JsonResponse({"error": "upload is incomplete"}, status=409)
        actual_size = sum(path.stat().st_size for path in part_paths)
        if actual_size != declared_size or actual_size > maximum_size:
            shutil.rmtree(upload_root, ignore_errors=True)
            return JsonResponse({"error": "file size does not match"}, status=400)

        final_directory = Path(settings.MEDIA_ROOT) / "stay_media" / str(stay.pk)
        final_directory.mkdir(parents=True, exist_ok=True)
        final_path = final_directory / f"{uuid4().hex}{extension}"
        with final_path.open("wb") as destination:
            for part_path in part_paths:
                with part_path.open("rb") as source:
                    shutil.copyfileobj(source, destination)
        shutil.rmtree(upload_root, ignore_errors=True)

        if not _content_matches_extension(final_path, extension):
            final_path.unlink(missing_ok=True)
            return JsonResponse({"error": "file content is invalid"}, status=400)

        relative_path = final_path.relative_to(Path(settings.MEDIA_ROOT))
        media = BookingStayMedia.objects.create(
            stay=stay,
            file=str(relative_path),
            content_type=expected_content_type,
            original_filename=original_filename[:255],
            size=actual_size,
            created_by=request.user,
        )
        return JsonResponse(
            {
                "completed": True,
                "id": media.pk,
                "url": reverse(
                    "hotel:stay_media_download",
                    args=[stay.pk, media.pk],
                ),
                "content_type": media.content_type,
                "filename": media.original_filename,
                "delete_url": reverse(
                    "hotel:stay_media_delete",
                    args=[stay.pk, media.pk],
                ),
            }
        )


class StayMediaDownloadView(LoginRequiredMixin, View):
    def get(self, request, pk, media_id):
        media = BookingStayMedia.objects.select_related("stay__booking__customer").filter(
            pk=media_id, stay_id=pk
        )
        if not request.user.is_staff:
            customer = getattr(request.user, "customer_profile", None)
            media = media.filter(stay__booking__customer=customer) if customer else media.none()
        media = get_object_or_404(media)

        response = FileResponse(
            media.file.open("rb"),
            content_type=media.content_type or "application/octet-stream",
            as_attachment=False,
            filename=media.original_filename or None,
        )
        response["Cache-Control"] = "private, no-store"
        return response


@method_decorator(csrf_protect, name="dispatch")
class StayMediaDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk, media_id):
        if not request.user.is_staff:
            return HttpResponseForbidden()
        media = get_object_or_404(BookingStayMedia, pk=media_id, stay_id=pk)
        media.file.delete(save=False)
        media.delete()
        return JsonResponse({"ok": True})


def _content_matches_extension(path, extension):
    if extension in {".gif", ".jpeg", ".jpg", ".png", ".webp"}:
        expected_formats = {
            ".gif": {"GIF"},
            ".jpeg": {"JPEG"},
            ".jpg": {"JPEG"},
            ".png": {"PNG"},
            ".webp": {"WEBP"},
        }
        try:
            with Image.open(path) as image:
                actual_format = image.format
                image.verify()
        except (OSError, UnidentifiedImageError):
            return False
        return actual_format in expected_formats[extension]

    with path.open("rb") as media_file:
        header = media_file.read(32)
    if extension in {".mov", ".mp4"}:
        return len(header) >= 12 and header[4:8] == b"ftyp"
    if extension == ".webm":
        return header.startswith(b"\x1aE\xdf\xa3")
    return False
