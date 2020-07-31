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
    unquote_filename, split_URI, is_URI, beautify_uri
from soundconverter.util.settings import get_gio_settings
from soundconverter.util.formats import get_file_extension
from soundconverter.gstreamer.profiles import audio_profiles_dict

basename_patterns = [
    ('%(.inputname)s', _('Same as input, but replacing the suffix')),
    ('%(.inputname)s%(.ext)s', _('Same as input, but with an additional suffix')),
    ('%(track-number)02d-%(title)s', _('Track number - title')),
    ('%(title)s', _('Track title')),
    ('%(artist)s-%(title)s', _('Artist - title')),
    ('Custom', _('Custom filename pattern')),
]

subfolder_patterns = [
    ('%(album-artist)s/%(album)s', _('artist/album')),
    ('%(album-artist)s-%(album)s', _('artist-album')),
    ('%(album-artist)s - %(album)s', _('artist - album')),
]


def get_basename_pattern():
    """Get the currently selected or custom filename pattern.

    For example '%(artist)s-%(title)s', without target extension.

    A custom-filename-pattern can also serve the purpose of a subfolder-pattern
    by having forward slashes.
    """
    settings = get_gio_settings()

    index = settings.get_int('name-pattern-index')
    if index >= len(basename_patterns):
        index = 0

    if index == len(basename_patterns) - 1:
        return settings.get_string('custom-filename-pattern')
    else:
        # an index of -1 selects the last entry on purpose
        return basename_patterns[index][0]


def get_subfolder_pattern():
    """Get the currently selected subfolder pattern.

    For example '%(album-artist)s/%(album)s', to create those new
    subfolders in the slected_folder based on tags.
    """
    settings = get_gio_settings()
    index = settings.get_int('subfolder-pattern-index')
    if index >= len(subfolder_patterns):
        index = 0
    return subfolder_patterns[index][0]


class TargetNameGenerator:
    """Generator for creating the target name from an input name.

    Create this class every time when the queue for conversion starts,
    because it remembers all relevant settings to avoid affecting the name
    generation of a running conversion by changing them in the ui.

    This class, once created, can create the names for all conversions in the
    queue, there is no need to create one TargetNameGenerator per Converter.
    """
    def __init__(self):
        # remember settings from when TargetNameGenerator was created:
        settings = get_gio_settings()
        self.same_folder_as_input = settings.get_boolean('same-folder-as-input')
        self.selected_folder = settings.get_string('selected-folder')
        self.output_mime_type = settings.get_string('output-mime-type')
        self.audio_profile = settings.get_string('audio-profile')
        self.vorbis_oga_extension = settings.get_boolean('vorbis-oga-extension')
        self.create_subfolders = settings.get_boolean('create-subfolders')
        self.replace_messy_chars = settings.get_boolean('replace-messy-chars')
        self.subfolder_pattern = get_subfolder_pattern()
        self.basename_pattern = get_basename_pattern()
        self.suffix = get_file_extension(self.output_mime_type)

        # Enforcing such rules helps to avoid undefined and untested
        # behaviour of functions.

        # If you wish to provide an uri scheme like ftp:// it should be in
        # the selected_folder instead.
        if is_URI(self.basename_pattern) or is_URI(self.subfolder_pattern):
            raise ValueError(
                'patterns should be patterns, not complete URIs.'
            )

        if not self.same_folder_as_input and not is_URI(self.selected_folder):
            raise ValueError(
                'your selected folder {} should be in URI format.'.format(
                    self.selected_folder
                )
            )

    @staticmethod
    def _unicode_to_ascii(unicode_string):
        # thanks to http://code.activestate.com/recipes/251871/
        return str(unicodedata.normalize('NFKD', unicode_string).encode('ASCII', 'ignore'), 'ASCII')

    @staticmethod
    def safe_string(name):
        """Replace all special characters in a string.

        Replace all characters that are not ascii, digits or '.' '-' '_' '/'
        with '_'. Umlaute will be changed to their closest non-umlaut
        counterpart.

        Will not break URI schemes.
        """
        scheme, name = split_URI(name)
        nice_chars = string.ascii_letters + string.digits + '.-_/'
        return (scheme or '') + ''.join([
            c if c in nice_chars else '_' for c in name
        ])

    @staticmethod
    def safe_name(child, parent=None):
        """Make a filename without dangerous special characters.

        Returns an absolute path.

        Replace all characters that are not ascii, digits or '.' '-' '_' '/'
        with '_'. Umlaute will be changed to their closest non-umlaut
        counterpart. Will not be applied on the part of the path that already
        exists, as that part apparently is already safe.

        Parameters
        ----------
        child : string
            Part of the path that needs to be modified to be safe. If it
            starts with a leading '/', it is considered absolute and will
            only keep the parents URI scheme
        parent : string
            Part of filename starting from the beginning of it that should be
            considered safe already and not further modified. May contain
            URI parts such as 'file://'.
        """
        if len(child) == 0:
            raise ValueError('empty filename')
        if is_URI(child):
            raise ValueError(
                'expected child "{}" to be a child path, '.format(child) +
                'not an URI'
            )
        if parent is not None and child.startswith(parent):
            raise ValueError(
                'wrong usage. Child "{}" should be the child '.format(child) +
                'of parent "{}", not the whole path'.format(child)
            )

        if child.startswith('/'):
            # child is absolute. keep only the uri scheme of parent
            parent = split_URI(parent or '')[0] or ''
        else:
            # make sure it can be added with the child, since os.path.join
            # cannot be used on uris (file:/// is not an absolute url for os),
            # by adding a trailing forward slash.
            if parent is None:
                parent = os.path.realpath('.') + '/'
            if not parent.endswith('/'):
                parent = parent + '/'

        # those are not needed and make problems in os.path.join.
        # os.path.realpath cannot be used to resolve dots because it destroys
        # the uri scheme.
        if child.startswith('./'):
            child = child[2:]
        if child.startswith('.'):
            child = child[1:]

        # figure out how much of the path already exists
        # split into for example [/test', '/baz.flac'] or ['qux.mp3']
        split = [s for s in re.split(r'((?:/|^)[^/]+)', child) if s != '']
        safe = ''
        while len(split) > 0:
            part = split.pop(0)
            if vfs_exists(parent + safe + part):
                safe += part
            else:
                # put the remaining unknown non-existing path back together
                # and make it safe
                rest = part + ''.join(split)

                # the path is in uri format, so before applying safe_string,
                # unquote it.
                # otherwise '%20' becomes '_20' when it should be '_'
                rest = urllib.parse.unquote(rest)
                rest = TargetNameGenerator._unicode_to_ascii(rest)
                rest = TargetNameGenerator.safe_string(rest)
                # quoting it back to URI escape strings is not needed, because
                # _ is not a critical character.
                safe += rest
                break

        if parent:
            safe = parent + safe

        return safe

    def fill_pattern(self, sound_file, pattern):
        """Fill tags into a filename pattern for sound_file.

        Parameters
        ----------
        sound_file : SoundFile
        pattern : string
            For example '%(album-artist)s/%(album)s/%(title)s'
        """
        tags = sound_file.tags

        # the pattern might be in an URI
        uri_prefix, pattern = split_URI(pattern)

        filename = beautify_uri(sound_file.uri)
        filename = os.path.basename(filename)
        filename, ext = os.path.splitext(filename)
        assert '/' not in filename
        d = {
            '.inputname': filename,
            '.ext': ext,
            '.target-ext': self.suffix[1:],
            'album': _('Unknown Album'),
            'artist': _('Unknown Artist'),
            'album-artist': _('Unknown Artist'),
            'title': filename,
            'track-number': 0,
            'track-count': 0,
            'genre': _('Unknown Genre'),
            'year': _('Unknown Year'),
            'date': _('Unknown Date'),
            'album-disc-number': 0,
            'album-disc-count': 0,
        }

        for key in tags:
            d[key] = tags[key]
            if isinstance(d[key], str):
                # take care of tags containing slashes
                d[key] = d[key].replace('/', '-')
                if key.endswith('-number'):
                    d[key] = int(d[key])

        # when artist set & album-artist not, use artist for album-artist
        if 'artist' in tags and 'album-artist' not in tags:
            d['album-artist'] = tags['artist']

        # add timestamp to substitution dict -- this could be split into more
        # entries for more fine-grained control over the string by the user...
        timestamp_string = time.strftime('%Y%m%d_%H_%M_%S')
        d['timestamp'] = timestamp_string

        # now fill the tags in the pattern with values:
        result = pattern % d

        if uri_prefix is not None:
            result = filename_to_uri(result, uri_prefix)

        return result

    def generate_temp_path(self, soundfile):
        """Generate a random filename that doesn't exist yet."""
        folder, basename = os.path.split(soundfile.uri)
        if not self.same_folder_as_input:
            folder = self.selected_folder
            folder = urllib.parse.quote(folder, safe='/:@')
        while True:
            rand = str(random())[-6:]
            filename = folder + '/' + basename + '~' + rand + '~SC~'
            if self.replace_messy_chars:
                filename = TargetNameGenerator.safe_name(filename)
            if not vfs_exists(filename):
                return filename

    def _get_target_subfolder(self, sound_file):
        """Get subfolders that should be created for the target file.

        They may also already exist, for example because a previous conversion
        created them.
        """
        subfolder = None
        if self.create_subfolders:
            subfolder = self.fill_pattern(sound_file, self.subfolder_pattern)
        elif sound_file.subfolders is not None and '/' not in self.basename_pattern:
            # use existing subfolders between base_path and the soundfile, but
            # only if the basename_pattern does not create subfolders.
            # For example:
            # .subfolders may have a structure of artist/album,
            # whereas basename might create a new structure of year/artist
            subfolder = os.path.join(sound_file.subfolders)
        return subfolder

    def _get_common_target_uri(self, sound_file):
        """Get the directory into which all files are converted."""
        if self.same_folder_as_input:
            parent = sound_file.base_path
        else:
            parent = self.selected_folder
        # ensure it is an URI, possibly adding file://
        return filename_to_uri(parent)

    def _get_target_filename(self, sound_file):
        """Get the output filename for the soundfile."""
        # note, that basename_pattern might actually contain subfolders, so
        # it's not always only a basename.
        # It's the deepest part of the target path though.
        return '{}.{}'.format(
            self.fill_pattern(sound_file, self.basename_pattern),
            self.suffix
        )

    def generate_target_path(self, sound_file, for_display=False):
        """Generate a target filename in URI format based on the settings.

        Patterns will be populated with tags.

        Parameters
        ----------
        sound_file : SoundFile
        for_display : bool
            Format it nicely in order to print it somewhere
        """
        # the beginning of the uri that all soundfiles will have in common
        # does not need to be processed in safe_name.
        parent_uri = self._get_common_target_uri(sound_file)
        # custom subfolders and such, changes depending on the soundfile
        subfolder = self._get_target_subfolder(sound_file)
        # filename, also changes depending on the soundfile
        filename = self._get_target_filename(sound_file)

        # put together
        if subfolder is not None:
            child = os.path.join(subfolder, filename)
        else:
            child = filename
        # child should be quoted to form a proper URI together with parent
        child = urllib.parse.quote(child)

        # subfolder and basename need to be cleaned
        if self.replace_messy_chars:
            path = self.safe_name(child, parent_uri)
        else:
            path = os.path.join(parent_uri, child)

        if for_display:
            return beautify_uri(path)
        else:
            return path
