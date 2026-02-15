from django.conf import settings
from django.db import models
from django.utils.crypto import get_random_string
from django.utils.text import slugify


class Tournament(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PUBLISHED = "PUBLISHED", "Published"
        ARCHIVED = "ARCHIVED", "Archived"

    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tournaments",
    )

    name = models.CharField(max_length=150)
    season_year = models.PositiveIntegerField(null=True, blank=True)
    city = models.CharField(max_length=80, null=True, blank=True)
    venue = models.CharField(max_length=120, null=True, blank=True)

    slug = models.SlugField(
        max_length=180,
        unique=True,
        editable=False,
    )

    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(f"{self.name}-{self.season_year}") if self.season_year else slugify(self.name)
            base = base or "tournament"

            # Try base, then base-2, base-3, ...
            candidate = base
            i = 2
            while Tournament.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base}-{i}"
                i += 1

                # Safety fallback (extremely rare unless you have tons of duplicates)
                if i > 200:
                    candidate = f"{base}-{get_random_string(6).lower()}"
                    break

            self.slug = candidate

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.season_year})" if self.season_year else self.name

    class Meta:
        db_table = "tournament"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organizer", "status"]),
            models.Index(fields=["slug"]),
        ]
