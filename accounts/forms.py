from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth import forms as auth_forms

from . import models


class UserChangeForm(auth_forms.UserChangeForm):
    class Meta:
        model = get_user_model()
        fields = [
            'username',
            'password',
            'first_name',
            'last_name',
            'email'
        ]

    def save(self, commit: bool = True):
        instance = super().save(commit)
        account, created = models.Account.objects.get_or_create(user=instance)
        return instance
