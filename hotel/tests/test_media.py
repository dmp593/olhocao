import io
import tempfile
from pathlib import Path
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from hotel.models import BookingStayMedia
from hotel.tests.factories import create_account, create_booking


def png_bytes():
    output = io.BytesIO()
    Image.new("RGB", (2, 2), color="blue").save(output, format="PNG")
    return output.getvalue()


class StayMediaUploadTests(TestCase):
    def setUp(self):
        self.temporary_media = tempfile.TemporaryDirectory()
        self.override = override_settings(
            MEDIA_ROOT=self.temporary_media.name,
            STAY_MEDIA_MAX_UPLOAD_BYTES=1024,
        )
        self.override.enable()
        self.customer = create_account()
        self.staff = create_account(username="staff", staff=True)
        self.booking, self.stay = create_booking(self.customer)
        self.url = reverse("hotel:stay_media_upload", kwargs={"pk": self.stay.pk})

    def tearDown(self):
        self.override.disable()
        self.temporary_media.cleanup()

    def upload(self, content, *, filename="photo.png", content_type="image/png"):
        return self.client.post(
            self.url,
            {
                "chunk": SimpleUploadedFile("chunk", content),
                "upload_id": str(uuid4()),
                "chunk_index": "0",
                "total_chunks": "1",
                "size": str(len(content)),
                "filename": filename,
                "content_type": content_type,
            },
        )

    def test_only_staff_can_upload(self):
        self.client.force_login(self.customer.user)

        response = self.upload(png_bytes())

        self.assertEqual(response.status_code, 403)
        self.assertEqual(BookingStayMedia.objects.count(), 0)

    def test_valid_image_content_is_stored_under_generated_name(self):
        self.client.force_login(self.staff.user)

        response = self.upload(png_bytes(), filename="../../customer-photo.png")

        self.assertEqual(response.status_code, 200)
        media = BookingStayMedia.objects.get()
        self.assertEqual(media.original_filename, "customer-photo.png")
        self.assertTrue(media.file.name.startswith(f"stay_media/{self.stay.pk}/"))
        self.assertNotIn("..", media.file.name)
        self.assertTrue(Path(media.file.path).is_file())

    def test_declared_image_with_invalid_bytes_is_rejected(self):
        self.client.force_login(self.staff.user)

        response = self.upload(b"this is not an image")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(BookingStayMedia.objects.count(), 0)

    def test_extension_and_declared_content_type_must_match(self):
        self.client.force_login(self.staff.user)

        response = self.upload(
            png_bytes(),
            filename="photo.jpg",
            content_type="image/png",
        )

        self.assertEqual(response.status_code, 400)

    def test_declared_size_above_limit_is_rejected_before_writing(self):
        self.client.force_login(self.staff.user)
        content = b"x" * 1025

        response = self.upload(content)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(BookingStayMedia.objects.count(), 0)

    def test_only_staff_can_delete_media(self):
        media = BookingStayMedia.objects.create(
            stay=self.stay,
            file=f"stay_media/{self.stay.pk}/photo.png",
            content_type="image/png",
            original_filename="photo.png",
            size=10,
            created_by=self.staff.user,
        )
        delete_url = reverse(
            "hotel:stay_media_delete",
            kwargs={"pk": self.stay.pk, "media_id": media.pk},
        )
        self.client.force_login(self.customer.user)
        self.assertEqual(self.client.post(delete_url).status_code, 403)
        self.client.force_login(self.staff.user)

        response = self.client.post(delete_url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(BookingStayMedia.objects.filter(pk=media.pk).exists())

    def test_booking_owner_can_download_media_but_another_customer_cannot(self):
        media_path = Path(self.temporary_media.name) / "stay_media" / str(self.stay.pk)
        media_path.mkdir(parents=True)
        (media_path / "photo.png").write_bytes(png_bytes())
        media = BookingStayMedia.objects.create(
            stay=self.stay,
            file=f"stay_media/{self.stay.pk}/photo.png",
            content_type="image/png",
            original_filename="photo.png",
            size=len(png_bytes()),
            created_by=self.staff.user,
        )
        download_url = reverse(
            "hotel:stay_media_download",
            kwargs={"pk": self.stay.pk, "media_id": media.pk},
        )
        self.client.force_login(self.customer.user)

        response = self.client.get(download_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")

        other_customer = create_account(username="other-customer")
        self.client.force_login(other_customer.user)
        self.assertEqual(self.client.get(download_url).status_code, 404)
