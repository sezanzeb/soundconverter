#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# SoundConverter - GNOME application for converting between audio formats.
# Copyright 2004 Lars Wirzenius
# Copyright 2005-2020 Gautier Portet
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 3 of the License.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307
# USA

"""Utils for generating names."""

import string
import time
import re
import os
from random import random
import urllib.request
import urllib.parse
import urllib.error
import unicodedata
from gettext import gettext as _
from soundconverter.util.fileoperations import vfs_exists, filename_to_uri, \
    unquote_filename
from soundconverter.util.settings import get_gio_settings
from soundconverter.audio.profiles import audio_profiles_dict


class TargetNameGenerator:
    """Generator for creating the target name from an input name."""

    def __init__(self):
        self.folder = None
        self.subfolders = ''
        self.basename = '%(.inputname)s'
        self.ext = '%(.ext)s'
        self.suffix = None
        self.replace_messy_chars = False
        self.max_tries = 2
        self.exists = vfs_exists

    @staticmethod
    def _unicode_to_ascii(unicode_string):
        # thanks to http://code.activestate.com/recipes/251871/
        return str(unicodedata.normalize('NFKD', unicode_string).encode('ASCII', 'ignore'), 'ASCII')

    @staticmethod
    def safe_name(filename):
        """Make a filename without dangerous special characters.

        Replace all characters that are not ascii, digits or '.' '-' '_' '/'
        with '_'. Umlaute will be changed to their closest non-umlaut
        counterpart. Will not be applied on the part of the path that already
        exists, as that part apparently is already safe.

        Parameters
        ----------
        filename : string
            Can be an URI or a normal path
        """
        if len(filename) == 0:
            raise ValueError('empty filename')

        nice_chars = string.ascii_letters + string.digits + '.-_/'

        scheme = ''
        # don't break 'file://' and keep the original scheme
        match = re.match(r'^([a-zA-Z]+://){0,1}(.+)', filename)
        if match[1]:
            # it's an URI!
            scheme = match[1]
            filename = match[2]
            filename = unquote_filename(filename)

        # figure out how much of the path already exists
        # split into for example [/test', '/baz.flac'] or ['qux.mp3']
        split = [s for s in re.split(r'((?:/|^)[^/]+)', filename) if s != '']
        safe = ''
        while len(split) > 0:
            part = split.pop(0)
            if os.path.exists(safe + part):
                safe += part
            else:
                # put the remaining unknown non-existing path back together
                # and make it safe
                non_existing = TargetNameGenerator._unicode_to_ascii(
                    part + ''.join(split)
                )
                non_existing = ''.join([
                    c if c in nice_chars else '_' for c in non_existing
                ])
                safe += non_existing
                break

        if scheme:
            safe = filename_to_uri(scheme + safe)

        return safe

    def get_target_name(self, sound_file):
        """Fill tags into a filename pattern for sound_file."""
        assert self.suffix, 'you just forgot to call set_target_suffix()'

        root = sound_file.base_path
        filename = sound_file.filename
        basename, ext = os.path.splitext(urllib.parse.unquote(filename))

        # make sure basename contains only the filename
        basefolder, basename = os.path.split(basename)

        d = {
            '.inputname': basename,
            '.ext': ext,
            '.target-ext': self.suffix[1:],
            'album': _('Unknown Album'),
            'artist': _('Unknown Artist'),
            'album-artist': _('Unknown Artist'),
            'title': basename,
            'track-number': 0,
            'track-count': 0,
            'genre': _('Unknown Genre'),
            'year': _('Unknown Year'),
            'date': _('Unknown Date'),
            'album-disc-number': 0,
            'album-disc-count': 0,
        }
        for key in sound_file.tags:
            d[key] = sound_file.tags[key]
            if isinstance(d[key], str):
                # take care of tags containing slashes
                d[key] = d[key].replace('/', '-')
                if key.endswith('-number'):
                    d[key] = int(d[key])
        # when artist set & album-artist not, use artist for album-artist
        if 'artist' in sound_file.tags and 'album-artist' not in sound_file.tags:
            d['album-artist'] = sound_file.tags['artist']

        # add timestamp to substitution dict -- this could be split into more
        # entries for more fine-grained control over the string by the user...
        timestamp_string = time.strftime('%Y%m%d_%H_%M_%S')
        d['timestamp'] = timestamp_string

        pattern = os.path.join(self.subfolders, self.basename + self.suffix)
        # now fill the tags in the pattern with values:
        result = pattern % d

        if self.replace_messy_chars:
            result = self.safe_name(result)

        if self.folder is None:
            folder = root
        else:
            folder = urllib.parse.quote(self.folder, safe='/:%@')

        if '/' in pattern:
            # we are creating folders using tags, disable basefolder handling
            basefolder = ''

        basefolder_quoted = urllib.parse.quote(basefolder)
        result_quoted = urllib.parse.quote(result)
        result = os.path.join(folder, basefolder_quoted, result_quoted)

        return result


def generate_temp_filename(soundfile):
    """Generate a random filename that doesn't exist yet."""
    gio_settings = get_gio_settings()
    folder, basename = os.path.split(soundfile.uri)
    if not gio_settings.get_boolean('same-folder-as-input'):
        folder = gio_settings.get_string('selected-folder')
        folder = urllib.parse.quote(folder, safe='/:@')
    while True:
        filename = folder + '/' + basename + '~' + str(random())[-6:] + '~SC~'
        if gio_settings.get_boolean('replace-messy-chars'):
            filename = TargetNameGenerator.safe_name(filename)
        if not vfs_exists(filename):
            return filename


def get_output_suffix():
    """Return the output file extension based on gio settings."""
    settings = get_gio_settings()
    output_type = settings.get_string('output-mime-type')
    profile = settings.get_string('audio-profile')
    profile_ext = audio_profiles_dict[profile][1] if profile else ''
    output_suffix = {
        'audio/x-vorbis': '.ogg',
        'audio/x-flac': '.flac',
        'audio/x-wav': '.wav',
        'audio/mpeg': '.mp3',
        'audio/x-m4a': '.m4a',
        'audio/ogg; codecs=opus': '.opus',
        'gst-profile': '.' + profile_ext,
    }.get(output_type, '.?')
    if output_suffix == '.ogg' and settings.get_boolean('vorbis-oga-extension'):
        output_suffix = '.oga'
    return output_suffix


def generate_filename(
    sound_file, basename_pattern, subfolder_pattern, for_display=False
):
    """Generate a target filename based on patterns and settings.

    Parameters
    ----------
    basename_pattern : string
        For example '%(artist)s-%(title)s'
    subfolder_pattern : string
        For example '%(album-artist)s/%(album)s'
    for_display : bool
    """
    settings = get_gio_settings()
    generator = TargetNameGenerator()
    generator.suffix = get_output_suffix()

    if not settings.get_boolean('same-folder-as-input'):
        folder = settings.get_string('selected-folder')
        folder = urllib.parse.quote(folder, safe='/:@')
        folder = filename_to_uri(folder)
        generator.folder = folder

        if settings.get_boolean('create-subfolders'):
            generator.subfolders = subfolder_pattern

    generator.basename = basename_pattern

    if for_display:
        generator.replace_messy_chars = False
        return unquote_filename(generator.get_target_name(sound_file))
    else:
        generator.replace_messy_chars = settings.get_boolean('replace-messy-chars')
        return generator.get_target_name(sound_file)
