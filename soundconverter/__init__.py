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


import sys


old_hook = sys.excepthook


def uncaught_hook(exctype, value, traceback):
    """To execute code when an exception is not caught."""
    if hasattr(value, 'uncaught_hook'):
        if callable(value.uncaught_hook):
            # execute it in the next glib tick, because if the next
            # task is synchronous, the error will otherwise be thrown from
            # within uncaught_hook and then uncaught_hook is not called
            # anymore. Errors from within this idle_add will call this
            # uncaught_hook later from scratch.
            try:
                value.uncaught_hook()
            except Exception as e:
                sys.excepthook(type(e), e, sys.last_traceback)
                return
    # Errors from within the GLib mainloop won't terminate the program,
    # so calling the old excepthook is safe.
    return old_hook(exctype, value, traceback)


sys.excepthook = uncaught_hook
