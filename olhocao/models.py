from django.db import models
from django.db.models.deletion import ProtectedError


class ImmutableFinancialQuerySet(models.QuerySet):
    def delete(self):
        raise ProtectedError(
            "Financial and audit records cannot be deleted; create a corrective record instead.",
            (),
        )


class ImmutableFinancialRecord(models.Model):
    """Application-level deletion guard for append-only financial records."""

    objects = ImmutableFinancialQuerySet.as_manager()

    class Meta:
        abstract = True

    def delete(self, *args, **kwargs):
        raise ProtectedError(
            "Financial and audit records cannot be deleted; create a corrective record instead.",
            (self,),
        )
