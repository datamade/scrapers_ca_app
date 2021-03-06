# coding: utf-8
from __future__ import unicode_literals

import codecs
import csv
import importlib
import os
import re
import sys

from boto.s3.connection import S3Connection
from boto.s3.key import Key
from django.core.management.base import BaseCommand
from django.template.defaultfilters import slugify
from opencivicdata.models import Membership
from six import StringIO
from six.moves.urllib.parse import urlsplit

from reports.models import Report
from reports.utils import get_offices, get_personal_url, module_name_to_metadata, remove_suffix_re


class Command(BaseCommand):
    help = 'Generates and uploads CSV files to S3'

    def handle(self, *args, **options):
        def save(key, body):
            k = Key(bucket)
            k.key = key
            k.set_contents_from_string(body)
            k.set_acl('public-read')

        sys.path.append(os.path.abspath('scrapers'))

        bucket = S3Connection().get_bucket('represent.opennorth.ca')

        names = {
            'Parliament of Canada': 'house-of-commons',
            'Legislative Assembly of Alberta': 'alberta-legislature',
            'Legislative Assembly of British Columbia': 'bc-legislature',
            'Legislative Assembly of Manitoba': 'manitoba-legislature',
            'Legislative Assembly of New Brunswick': 'new-brunswick-legislature',
            'Newfoundland and Labrador House of Assembly': 'newfoundland-labrador-legislature',
            'Nova Scotia House of Assembly': 'nova-scotia-legislature',
            'Legislative Assembly of Ontario': 'ontario-legislature',
            'Legislative Assembly of Prince Edward Island': 'pei-legislature',
            'Assemblée nationale du Québec': 'quebec-assemblee-nationale',
            'Legislative Assembly of Saskatchewan': 'saskatchewan-legislature',
        }

        default_headers = [
            'District name',
            'Primary role',
            'Name',  # not in CSV schema
            'First name',
            'Last name',
            'Gender',
            'Party name',
            'Email',
            'Photo URL',
            'Source URL',
            'Website',
            'Facebook',
            'Instagram',
            'Twitter',
            'LinkedIn',
            'YouTube',
        ]
        office_headers = [
            'Office type',  # not in CSV schema
            'Address',  # not in CSV schema
            'Phone',
            'Fax',
        ]

        all_rows = []
        max_offices_count = 0

        reports = Report.objects.filter(exception='').exclude(module__endswith='_candidates').exclude(module__endswith='_municipalities').order_by('module')
        for report in reports:
            try:
                metadata = module_name_to_metadata(report.module)

                rows = []
                offices_count = 0

                # Exclude party memberships.
                queryset = Membership.objects.filter(organization__jurisdiction_id=metadata['jurisdiction_id']).exclude(role__in=('member', 'candidate'))
                for membership in queryset.prefetch_related('contact_details', 'person', 'person__links', 'person__sources'):
                    person = membership.person

                    try:
                        party_name = Membership.objects.get(organization__classification='party', role='member', person=person).organization.name
                    except Membership.DoesNotExist:
                        party_name = None

                    facebook = None
                    instagram = None
                    linkedin = None
                    twitter = None
                    youtube = None
                    for link in person.links.all():
                        domain = '.'.join(urlsplit(link.url).netloc.split('.')[-2:])
                        if domain in ('facebook.com', 'fb.com'):
                            facebook = link.url
                        elif domain == 'instagram.com':
                            instagram = link.url
                        elif domain == 'linkedin.com':
                            linkedin = link.url
                        elif domain == 'twitter.com':
                            twitter = link.url
                        elif domain == 'youtube.com':
                            youtube = link.url

                    if person.gender == 'male':
                        gender = 'M'
                    elif person.gender == 'female':
                        gender = 'F'
                    else:
                        gender = None

                    if ' ' in person.name:
                        first_name, last_name = person.name.rsplit(' ', 1)
                    else:
                        first_name, last_name = None, person.name

                    # @see https://represent.opennorth.ca/api/#fields
                    sources = list(person.sources.all())
                    row = [
                        remove_suffix_re.sub('', membership.post.label),  # District name
                        membership.role,  # Elected office
                        person.name,  # Name
                        first_name,  # First name
                        last_name,  # Last name
                        gender,  # Gender
                        party_name,  # Party name
                        next((contact_detail.value for contact_detail in membership.contact_details.all() if contact_detail.type == 'email'), None),  # Email
                        person.image,  # Photo URL
                        sources[-1].url if len(sources) > 1 else None,  # Source URL
                        get_personal_url(person),  # Website
                        facebook,  # Facebook
                        instagram,  # Instagram
                        twitter,  # Twitter
                        linkedin,  # LinkedIn
                        youtube,  # YouTube
                    ]

                    offices = get_offices(membership)
                    if len(offices) > offices_count:
                        offices_count = len(offices)

                    for office in offices:
                        for key in ('type', 'postal', 'tel', 'fax'):
                            row.append(office.get(key))

                    # If the person is associated to multiple boundaries.
                    if re.search(r'\AWards\b', membership.post.label):
                        for district_id in re.findall(r'\d+', membership.post.label):
                            row = row[:]
                            row[0] = 'Ward %s' % district_id
                            rows.append(row)
                    else:
                        rows.append(row)

                rows.sort()

                headers = default_headers[:]
                for _ in range(offices_count):
                    headers += office_headers

                name = metadata['name']
                if name in names:
                    slug = names[name]
                else:
                    slug = slugify(name)

                io = StringIO()
                body = csv.writer(io)
                body.writerow(headers)
                body.writerows(rows)
                save('csv/%s.csv' % slug, codecs.encode(io.getvalue(), 'windows-1252'))

                if offices_count > max_offices_count:
                    max_offices_count = offices_count

                for row in rows:
                    row.insert(0, name)
                    all_rows.append(row)
            except ImportError:
                report.delete()  # delete reports for old modules

        headers = ['Organization'] + default_headers
        for _ in range(max_offices_count):
            headers += office_headers

        io = StringIO()
        body = csv.writer(io)
        body.writerow(headers)
        body.writerows(all_rows)
        save('csv/complete.csv', codecs.encode(io.getvalue(), 'windows-1252'))
