# Generated by Django 5.2 on 2025-04-23 22:09

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='ContactSubject',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='Contact Subject Name', max_length=100, verbose_name='Name')),
                ('name_en', models.CharField(help_text='Contact Subject Name', max_length=100, null=True, verbose_name='Name')),
                ('name_pt', models.CharField(help_text='Contact Subject Name', max_length=100, null=True, verbose_name='Name')),
                ('order', models.IntegerField(default=999, help_text='Contact Subject Order', verbose_name='Order')),
            ],
        ),
        migrations.CreateModel(
            name='ContactRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='Your Name', max_length=128, verbose_name='Name')),
                ('phone', models.CharField(help_text='Your Phone', max_length=32, verbose_name='Phone')),
                ('email', models.EmailField(help_text='Your Email Account', max_length=254, verbose_name='Email')),
                ('message', models.TextField(help_text='Your Message', verbose_name='Message')),
                ('read_at', models.DateTimeField(blank=True, null=True)),
                ('subject', models.ForeignKey(blank=True, help_text='Contact Subject', null=True, on_delete=django.db.models.deletion.SET_NULL, to='frontoffice.contactsubject', verbose_name='Subject')),
            ],
        ),
    ]
