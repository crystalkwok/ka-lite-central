"""
This command is the master command for language packs. Based on 
command line arguments provided, it calls all i18n commands
necessary to update language packs. 

1. Updates all cached srt files  from Amara
2. Downloads latest translations from CrowdIn
3. Generates metadata on language packs (subtitles and UI translations)
4. Compiles the UI translations
5. Zips up the packs and exposes them at a static url 
"""
import datetime
import glob
import json
import os
import re
import requests
import shutil
import zipfile
import StringIO

from optparse import make_option
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command

import settings
import version
from settings import LOG as logging
from shared.i18n import get_language_name, convert_language_code_format
from update_po import compile_all_po_files
from utils.general import ensure_dir, version_diff


LOCALE_ROOT = settings.LOCALE_PATHS[0]
LANGUAGE_PACK_AVAILABILITY_FILENAME = "language_pack_availability.json"

class Command(BaseCommand):
	help = 'Updates all language packs'

	option_list = BaseCommand.option_list + (
        make_option('-d', '--days',
                    action='store',
                    dest='days',
                    default=None,
                    metavar="NUM_DAYS",
                    help="Update any and all subtitles that haven't been refreshed in the numebr of days given. If not specified, subtitles will not be updated."),
	)
	
	def handle(self, **options):
		## Ensure that central server is using the correct schema to store srt files
		raise_if_outdated_srt_schema()
		if options["days"]:
			update_srts(options["days"])
		update_language_packs()


def update_srts(days):
	"""
	Run the commands to update subtitles that haven't been updated in the number of days provided.
	Default is to update all srt files that haven't been requested in 30 days
	"""
	date = '{0.month}/{0.day}/{0.year}'.format(datetime.date.today()-datetime.timedelta(int(days)))
	logging.info("Updating subtitles that haven't been refreshed since %s" % date)
	call_command("generate_subtitle_map", date_since_attempt=date)
	call_command("cache_subtitles", date_since_attempt=date, lang_code='all')

def update_language_packs():

	## Download latest UI translations from CrowdIn
	download_latest_translations() 

	## Loop through new UI translations & subtitles, create/update unified meta data
	generate_metadata()
	
	## Compile
	compile_all_po_files()
	
	## Zip
	zip_language_packs()


def raise_if_outdated_srt_schema():
	"""Raise CommandError if srt files have not been manually moved"""
	if os.path.exists(os.path.join(settings.STATIC_ROOT, "srt")):
		raise CommandError("Please run the command 'move_existing_srts' before continuing.")


def download_latest_translations(project_id=settings.CROWDIN_PROJECT_ID, project_key=settings.CROWDIN_PROJECT_KEY, language_code="all"):
	"""Download latest translations from CrowdIn to corresponding locale directory."""

	## Build latest package
	build_translations()

	## Get zip file of translations
	logging.info("Attempting to download a zip archive of current translations")
	request_url = "http://api.crowdin.net/api/project/%s/download/%s.zip?key=%s" % (project_id, language_code, project_key)
	r = requests.get(request_url)
	try:
		r.raise_for_status()
	except Exception as e:
		logging.error(e)
	else:
		logging.info("Successfully downloaded zip archive")

	## Unpack into temp dir
	z = zipfile.ZipFile(StringIO.StringIO(r.content))
	tmp_dir_path = os.path.join(LOCALE_ROOT, "tmp")
	z.extractall(tmp_dir_path)

	## Copy over new translations
	extract_new_po(tmp_dir_path)

	# Clean up tracks
	if os.path.exists(tmp_dir_path):
		shutil.rmtree(tmp_dir_path)


def build_translations(project_id=settings.CROWDIN_PROJECT_ID, project_key=settings.CROWDIN_PROJECT_KEY):
	"""Build latest translations into zip archive on CrowdIn"""

	logging.info("Requesting that CrowdIn build a fresh zip of our translations")
	request_url = "http://api.crowdin.net/api/project/%s/export?key=%s" % (project_id, project_key)
	r = requests.get(request_url)
	try:
		r.raise_for_status()
	except Exception as e:
		logging.error(e)


def extract_new_po(tmp_dir_path=os.path.join(LOCALE_ROOT, "tmp")):
	"""Move newly downloaded po files to correct location in locale direction"""

	logging.info("Unpacking new translations")
	for lang in os.listdir(tmp_dir_path):
		converted_code = convert_language_code_format(lang)
		# ensure directory exists in locale folder, and then overwrite local po files with new ones 
		ensure_dir(os.path.join(LOCALE_ROOT, converted_code, "LC_MESSAGES"))
		for po_file in glob.glob(os.path.join(tmp_dir_path, lang, "*/*.po")): 
			if "js" in os.path.basename(po_file):
				shutil.copy(po_file, os.path.join(LOCALE_ROOT, converted_code, "LC_MESSAGES", "djangojs.po"))
			else:
				shutil.copy(po_file, os.path.join(LOCALE_ROOT, converted_code, "LC_MESSAGES", "django.po"))


def generate_metadata():
	"""Loop through locale folder, create or update language specific meta and create or update master file."""

	logging.info("Generating new po file metadata")
	master_file = []

	# loop through all languages in locale, update master file
	crowdin_meta_dict = get_crowdin_meta()
	subtitle_counts = json.loads(open(settings.SUBTITLES_DATA_ROOT + "subtitle_counts.json").read())
	for lang in os.listdir(LOCALE_ROOT):
		# skips anything not a directory
		if not os.path.isdir(os.path.join(LOCALE_ROOT, lang)):
			continue
		crowdin_meta = next((meta for meta in crowdin_meta_dict if meta["code"] == convert_language_code_format(lang_code=lang, for_crowdin=True)), None)
		try: 
			local_meta = json.loads(open(os.path.join(LOCALE_ROOT, lang, "%s_metadata.json" % lang)).read())
		except:
			local_meta = {
				"code": crowdin_meta.get("code"),
				"name": crowdin_meta.get("name"),
			}

		# Obtain number of subtitles
		entry = subtitle_counts.get(get_language_name(lang))
		if entry:
			srt_count = entry.get("count")
		else:
			srt_count = 0


		updated_metadata = {
			"percent_translated": int(crowdin_meta.get("approved_progress")),
			"phrases": int(crowdin_meta.get("phrases")),
			"approved_translations": int(crowdin_meta.get("approved")),
			"software_version": version.VERSION,
			"crowdin_version": increment_crowdin_version(local_meta, crowdin_meta),
			"subtitle_count": srt_count,
		}

		local_meta.update(updated_metadata)

		# Write locally (this is used on download by distributed server to update it's database)
		with open(os.path.join(LOCALE_ROOT, lang, "%s_metadata.json" % lang), 'w') as output:
			json.dump(local_meta, output)
		
		# Update master (this is used for central server to handle API requests for data)
		master_file.append(local_meta)

	# Save updated master
	ensure_dir(settings.LANGUAGE_PACK_ROOT)
	with open(os.path.join(settings.LANGUAGE_PACK_ROOT, LANGUAGE_PACK_AVAILABILITY_FILENAME), 'w') as output:
		json.dump(master_file, output) 
	logging.info("Local record of translations updated")


def get_crowdin_meta(project_id=settings.CROWDIN_PROJECT_ID, project_key=settings.CROWDIN_PROJECT_KEY):
	"""Return tuple in format (total_strings, total_translated, percent_translated)"""

	request_url = "http://api.crowdin.net/api/project/%s/status?key=%s&json=True" % (project_id, project_key)
	r = requests.get(request_url)
	r.raise_for_status()

	crowdin_meta_dict = json.loads(r.content)
	return crowdin_meta_dict


def increment_crowdin_version(local_meta, crowdin_meta):
	"""Increment language pack version if translations have been updated (start over if software version has incremented)"""
	total_translated = local_meta.get("total_translated")
	if not total_translated or version_diff(local_meta.get("software_version"), version.VERSION) < 0:
		# set to one for the first time, or if this is the first build of a new software version
		crowdin_version = 1
	elif total_translated == crowdin_meta.get("approved"):
		crowdin_version = local_meta.get("crowdin_version") or 1
	else:
		crowdin_version = local_meta.get("crowdin_version") + 1
	return crowdin_version



def zip_language_packs():
	"""Zip up and expose all language packs"""
	logging.info("Zipping up language packs")
	ensure_dir(settings.LANGUAGE_PACK_ROOT)
	for lang in os.listdir(LOCALE_ROOT):
		if not os.path.isdir(os.path.join(LOCALE_ROOT, lang)):
			continue
		# Create a zipfile for this language
		zip_path = os.path.join(settings.LANGUAGE_PACK_ROOT, version.VERSION)
		ensure_dir(zip_path)
		z = zipfile.ZipFile(os.path.join(zip_path, "%s.zip" % convert_language_code_format(lang)), 'w')
		# Get every single file in the directory and zip it up
		lang_locale_path = os.path.join(LOCALE_ROOT, lang)
		for metadata_file in glob.glob('%s/*.json' % lang_locale_path):
			z.write(os.path.join(lang_locale_path, metadata_file), arcname=os.path.basename(metadata_file))	
		for mo_file in glob.glob('%s/LC_MESSAGES/*.mo' % lang_locale_path):
			z.write(os.path.join(lang_locale_path, mo_file), arcname=os.path.join("LC_MESSAGES", os.path.basename(mo_file)))	
		z.close()
	logging.info("Done.")





