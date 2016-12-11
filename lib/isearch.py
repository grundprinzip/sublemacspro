#####################################################
#            Better incremental search              #
#####################################################

import re
from copy import copy

import sublime, sublime_plugin

from sublemacspro.lib.misc import *

isearch_info = dict()
def isearch_info_for(view):
    if isinstance(view, sublime.Window):
        window = view
    else:
        window = view.window()
    if window:
        return isearch_info.get(window.id(), None)
    return None
def set_isearch_info_for(view, info):
    window = view.window()
    isearch_info[window.id()] = info
    return info
def clear_isearch_info_for(view):
    window = view.window()
    del(isearch_info[window.id()])

class ISearchInfo():
    last_search = None

    class StackItem():
        def __init__(self, search, regions, selected, current_index, forward, wrapped):
            self.prev = None
            self.search = search
            self.regions = regions
            self.selected = selected
            self.current_index = current_index
            self.forward = forward
            self.try_wrapped = False
            self.wrapped = wrapped
            if current_index >= 0 and regions:
                # add the new one to selected
                selected.append(regions[current_index])

        def get_point(self):
            if self.current_index >= 0:
                r = self.regions[self.current_index]
                return r.begin() if self.forward else r.end()
            return None

        #
        # Clone is called when we want to make progress with the same search string as before.
        #
        def clone(self):
            return copy.copy(self)

        #
        # Go to the next match of the current string. Keep means "keep the current location as a
        # future cursor" and forward is True if we're moving forward.
        #
        def step(self, forward, keep):
            index = self.current_index
            matches = len(self.regions)
            if (self.regions and (index < 0 or (index == 0 and not forward) or (index == matches - 1) and forward)):
                # wrap around!
                index = 0 if forward else matches - 1
                if self.try_wrapped or not self.regions:
                    wrapped = True
                    self.try_wrapped = False
                else:
                    self.try_wrapped = True
                    return None
            elif (forward and index < matches - 1) or (not forward and index > 0):
                index = index + 1 if forward else index - 1
                wrapped = self.wrapped
            else:
                return None
            selected = copy(self.selected)
            if not keep and len(selected) > 0:
                del(selected[-1])
            return ISearchInfo.StackItem(self.search, self.regions, selected, index, forward, wrapped)


    def __init__(self, view, forward, regex):
        self.view = view
        self.current = ISearchInfo.StackItem("", [], [], -1, forward, False)
        self.util = CmdUtil(view)
        self.window = view.window()
        self.point = self.util.get_cursors()
        self.update()
        self.input_view = None
        self.in_changes = 0
        self.forward = forward
        self.regex = regex

    def open(self):
        window = self.view.window()
        self.input_view = window.show_input_panel("%sI-Search:" % ("Regexp " if self.regex else "", ),
                                                  "", self.on_done, self.on_change, self.on_cancel)

    def on_done(self, val):
        # on_done: stop the search, keep the cursors intact
        self.finish(abort=False)

    def on_cancel(self):
        # on_done: stop the search, return cursor to starting point
        self.finish(abort=True)

    def on_change(self, val):
        if self.in_changes > 0:
            # When we pop back to an old state, we have to replace the search string with what was
            # in effect at that state. We do that by deleting all the text and inserting the value
            # of the search string. This causes this on_change method to be called. We want to
            # ignore it, which is what we're doing here.
            self.in_changes -= 1
            return

        if self.current and self.current.search == val:
            # sometimes sublime calls us when nothing has changed
            return

        self.find(val)

    def find(self, val):
        # determine if this is case sensitive search or not
        flags = 0 if self.regex else sublime.LITERAL
        if not re.search(r'[A-Z]', val):
            flags |= sublime.IGNORECASE

        # find all instances if we have a search string
        if len(val) > 0:
            regions = self.view.find_all(val, flags)

            # find the closest match to where we currently are
            pos = None
            if self.current:
                pos = self.current.get_point()
            if pos is None:
                pos = self.point[-1].b
            index = self.find_closest(regions, pos, self.forward)

            # push this new state onto the stack
            self.push(ISearchInfo.StackItem(val, regions, [], index, self.forward, self.current.wrapped))
        else:
            regions = None
            index = -1
        self.update()

    #
    # Implementation and internal API.
    #

    #
    # Push a new state onto the stack.
    #
    def push(self, item):
        item.prev = self.current
        self.current = item

    #
    # Pop one state of the stack and restore everything to the state at that time.
    #
    def pop(self):
        if self.current.prev:
            self.current = self.current.prev
            self.set_text(self.current.search)
            self.forward = self.current.forward
            self.update()
        else:
            print("Nothing to pop so not updating!")

    def hide_panel(self):
        # close the panel which should trigger an on_done
        window = self.view.window()
        if window:
            window.run_command("hide_panel")

    def done(self):
        self.finish()

    #
    # Set the text of the search to a particular value. If is_pop is True it means we're restoring
    # to a previous state. Otherwise, we want to pretend as though this text were actually inserted.
    #
    def set_text(self, text, is_pop=True):
        if is_pop:
            self.in_changes += 1
        v = self.input_view
        self.input_view.run_command("sbp_inc_search", {"cmd": "set_search", "text": text})

    #
    # Find the most recent stack item where we were not in the error state.
    #
    def not_in_error(self):
        si = self.current
        while si and not si.selected and si.search:
            si = si.prev
        return si

    def finish(self, abort=False):
        util = self.util
        if isearch_info_for(self.view) != self:
            return
        if self.current and self.current.search:
            ISearchInfo.last_search = self.current.search
        util.set_status("")

        point_set = False
        if not abort:
            selection = self.view.sel()
            selection.clear()
            current = self.current
            not_in_error = self.not_in_error()
            if current and current.selected:
                if not current.forward:
                    # put the cursor at the front of the each region
                    selected = (sublime.Region(s.b, s.a) for s in current.selected)
                else:
                    selected = current.selected
                selection.add_all(selected)
                point_set = True
            elif not_in_error and not_in_error.regions:
                selection.add_all([not_in_error.regions[not_in_error.current_index]])
                point_set = True

        if not point_set:
            # back whence we started
            util.set_cursors(self.point)
        else:
            util.set_mark(self.point, and_selection=False)

        # erase our regions
        self.view.erase_regions(REGION_FIND)
        self.view.erase_regions(REGION_SELECTED)
        clear_isearch_info_for(self.view)
        self.hide_panel()

    def update(self):
        si = self.current
        if si is None:
            return
        not_in_error = self.not_in_error()

        self.view.add_regions(REGION_FIND, si.regions, "text", "", sublime.DRAW_NO_FILL)
        selected = si.selected or (not_in_error.selected and [not_in_error.selected[-1]]) or []
        self.view.add_regions(REGION_SELECTED, selected, "string", "", sublime.DRAW_NO_OUTLINE)
        if selected:
            self.view.show(selected[-1])

        status = ""
        if si != not_in_error or si.try_wrapped:
            status += "Failing "
        if self.current.wrapped:
            status += "Wrapped "
        status += "I-Search " + ("Forward" if self.current.forward else "Reverse")
        if si != not_in_error:
            if len(self.current.regions) > 0:
                status += " %s %s" % (pluralize("match", len(self.current.regions), "es"), ("above" if self.forward else "below"))
        else:
            n_cursors = min(len(si.selected), len(si.regions))
            status += " %s, %s" % (pluralize("match", len(si.regions), "es"), pluralize("cursor", n_cursors))

        self.util.set_status(status)

    #
    # Try to make progress with the current search string. Even if we're currently failing (in our
    # current direction) it doesn't mean there aren't matches for what we've typed so far.
    #
    def next(self, keep, forward=None):
        if self.current.prev is None:
            # do something special if we invoke "i-search" twice at the beginning
            if ISearchInfo.last_search:
                # insert the last search string
                self.set_text(ISearchInfo.last_search, is_pop=False)
        else:
            if forward is None:
                forward = self.current.forward
            new = self.current.step(forward=forward, keep=keep)
            if new:
                self.push(new)
            self.update()

    def keep_all(self):
        while self.current.regions and self.current.current_index < len(self.current.regions):
            new = self.current.step(forward=self.current.forward, keep=True)
            if new:
                self.push(new)
            else:
                break
        self.update()

    def append_from_cursor(self):
        # Figure out the contents to the right of the last region in the current selected state, and
        # append characters from there.
        si = self.current
        if len(si.search) > 0 and not si.selected:
            # search is failing - no point in adding from current cursor!
            return

        view = self.view
        limit = view.size()
        if si.selected:
            # grab end of most recent item
            point = si.selected[-1].end()
        else:
            point = self.point[0].b
        if point >= limit:
            return

        # now push new states for each character we append to the search string
        helper = self.util
        search = si.search
        separators = settings_helper.get("sbp_word_separators", default_sbp_word_separators)
        case_sensitive = re.search(r'[A-Z]', search) is not None

        def append_one(ch):
            if not case_sensitive:
                ch = ch.lower()
            if self.regex and ch in "{}()[].*+":
                return "\\" + ch
            return ch

        if point < limit:
            # append at least one character, word character or not
            search += append_one(view.substr(point))
            point += 1
            self.on_change(search)

            # now insert word characters
            while point < limit and helper.is_word_char(point, True, separators):
                ch = view.substr(point)
                search += append_one(ch)
                self.on_change(search)
                point += 1
        self.set_text(self.current.search)

    def quit(self):
        close = False

        if self.current.regions:
            # if we have some matched regions, we're in "successful" state and close down the whole
            # thing
            close = True
        else:
            # here the search is currently failing, so we back up until the last non-failing state
            while self.current.prev and not self.current.prev.regions:
                self.current = self.current.prev
            if self.current.prev is None:
                close = True
        if close:
            self.finish(abort=True)
        else:
            self.pop()

    def find_closest(self, regions, pos, forward):
        #
        # The regions are sorted so clearly this would benefit from a simple binary search ...
        #
        if len(regions) == 0:
            return -1
        # find the first region after the specified pos
        found = False
        if forward:
            for index,r in enumerate(regions):
                if r.end() >= pos:
                    return index
            return -1
        else:
            for index,r in enumerate(regions):
                if r.begin() > pos:
                    return index - 1
            return len(regions) - 1
