###
# Copyright (c) 2006, Ilya Kuznetsov
# Copyright (c) 2008,2012 Kevin Funk
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

###

from __future__ import unicode_literals
import supybot.utils as utils
from supybot.commands import *
import supybot.conf as conf
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks
import supybot.world as world
import supybot.log as log

from xml.dom import minidom
from time import time
try:
    from itertools import izip # Python 2
except ImportError:
    izip = zip # Python 3

from .LastFMDB import *

class LastFMParser:

    def parseRecentTracks(self, stream):
        """
        <stream>

        Returns a tuple with the information of the last-played track.
        """
        xml = minidom.parse(stream).getElementsByTagName("recenttracks")[0]
        user = xml.getAttribute("user")

        try:
            t = xml.getElementsByTagName("track")[0] # most recent track
        except IndexError:
            return [user] + [None]*5
        isNowPlaying = (t.getAttribute("nowplaying") == "true")
        if not isNowPlaying:
            time = int(t.getElementsByTagName("date")[0].getAttribute("uts"))
        else:
            time = None

        artist = t.getElementsByTagName("artist")[0].firstChild.data
        track = t.getElementsByTagName("name")[0].firstChild.data
        try:
            albumNode = t.getElementsByTagName("album")[0].firstChild
            album = albumNode.data
        except (IndexError, AttributeError):
            album = None
        return (user, isNowPlaying, artist, track, album, time)

class LastFM(callbacks.Plugin):

    def __init__(self, irc):
        self.__parent = super(LastFM, self)
        self.__parent.__init__(irc)
        self.db = LastFMDB(dbfilename)
        world.flushers.append(self.db.flush)

        # 2.0 API (see http://www.lastfm.de/api/intro)
        self.apiKey = self.registryValue("apiKey")
        self.APIURL = "http://ws.audioscrobbler.com/2.0/?"

    def die(self):
        if self.db.flush in world.flushers:
            world.flushers.remove(self.db.flush)
        self.db.close()
        self.__parent.die()

    def lastfm(self, irc, msg, args, method, optionalId):
        """<method> [<id>]

        Lists LastFM info where <method> is in
        [friends, neighbours, profile, recenttracks, tags, topalbums,
        topartists, toptracks].
        Set your LastFM ID with the set method (default is your current nick)
        or specify <id> to switch for one call.
        """
        if not self.apiKey:
            irc.error("The API Key is not set for this plugin. Please set it via"
                      "config plugins.lastfm.apikey and reload the plugin. "
                      "You can sign up for an API Key using "
                      "http://www.last.fm/api/account/create", Raise=True)
        method = method.lower()
        knownMethods = {'friends': 'user.getFriends',
                        'neighbours': 'user.getNeighbours',
                        'profile': 'user.getInfo',
                        'recenttracks': 'user.getRecentTracks',
                        'tags': 'user.getTopTags',
                        'topalbums': 'user.getTopAlbums',
                        'topartists': 'user.getTopArtists',
                        'toptracks': 'user.getTopTracks'}
        if method not in knownMethods:
            irc.error("Unsupported method '%s'" % method, Raise=True)
        id = (optionalId or self.db.getId(msg.nick) or msg.nick)
        channel = msg.args[0]
        maxResults = self.registryValue("maxResults", channel)

        url = "%sapi_key=%s&method=%s&user=%s" % (self.APIURL,
            self.apiKey, knownMethods[method], id)
        try:
            f = utils.web.getUrlFd(url)
        except utils.web.Error:
            irc.error("Unknown ID (%s) or unknown method (%s)"
                    % (msg.nick, method), Raise=True)

        xml = minidom.parse(f).getElementsByTagName("lfm")[0]
        content = xml.childNodes[1].getElementsByTagName("name")
        results = [res.firstChild.nodeValue.strip() for res in content[0:maxResults*2]]
        if method in ('topalbums', 'toptracks'):
            # Annoying, hackish way of grouping artist+album/track items
            results = ["%s - %s" % (thing, artist) for thing, artist in izip(results[1::2], results[::2])]
        irc.reply("%s's %s: %s (with a total number of %i entries)"
                % (id, method, ", ".join(results[0:maxResults]),
                    len(content)))

    lastfm = wrap(lastfm, ["something", optional("something")])

    def nowPlaying(self, irc, msg, args, optionalId):
        """[<id>]

        Announces the now playing track of the specified LastFM ID.
        Set your LastFM ID with the set method (default is your current nick)
        or specify <id> to switch for one call.
        """

        if not self.apiKey:
            irc.error("The API Key is not set for this plugin. Please set it via"
                      "config plugins.lastfm.apikey and reload the plugin. "
                      "You can sign up for an API Key using "
                      "http://www.last.fm/api/account/create", Raise=True)
        id = (optionalId or self.db.getId(msg.nick) or msg.nick)

        # see http://www.lastfm.de/api/show/user.getrecenttracks
        url = "%sapi_key=%s&method=user.getrecenttracks&user=%s" % (self.APIURL, self.apiKey, id)
        try:
            f = utils.web.getUrlFd(url)
        except utils.web.Error:
            irc.error("Unknown ID (%s)" % id, Raise=True)

        parser = LastFMParser()
        (user, isNowPlaying, artist, track, album, time) = parser.parseRecentTracks(f)
        if track is None:
            irc.reply("%s doesn't seem to have listened to anything." % id)
            return
        albumStr = ("[%s]" % album) if album else ""
        if isNowPlaying:
            irc.reply('%s is listening to "%s" by %s %s'
                    % (user, track, artist, albumStr))
        else:
            irc.reply('%s listened to "%s" by %s %s more than %s'
                    % (user, track, artist, albumStr,
                        self._formatTimeago(time)))

    np = wrap(nowPlaying, [optional("something")])

    def setUserId(self, irc, msg, args, newId):
        """<id>

        Sets the LastFM ID for the caller and saves it in a database.
        """

        self.db.set(msg.nick, newId)

        irc.reply("LastFM ID changed.")
        self.profile(irc, msg, args)

    set = wrap(setUserId, ["something"])

    def profile(self, irc, msg, args, optionalId):
        """[<id>]

        Prints the profile info for the specified LastFM ID.
        Set your LastFM ID with the set method (default is your current nick)
        or specify <id> to switch for one call.
        """
        if not self.apiKey:
            irc.error("The API Key is not set for this plugin. Please set it via"
                      "config plugins.lastfm.apikey and reload the plugin. "
                      "You can sign up for an API Key using "
                      "http://www.last.fm/api/account/create", Raise=True)
        id = (optionalId or self.db.getId(msg.nick) or msg.nick)

        url = "%sapi_key=%s&method=user.getInfo&user=%s" % (self.APIURL, self.apiKey, id)
        try:
            f = utils.web.getUrlFd(url)
        except utils.web.Error:
            irc.error("Unknown user (%s)" % id, Raise=True)

        xml = minidom.parse(f).getElementsByTagName("user")[0]
        keys = ("realname", "registered", "age", "gender", "country", "playcount")
        profile = {"id": id}
        for tag in keys:
            try:
                profile[tag] = xml.getElementsByTagName(tag)[0].firstChild.data.strip()
            except AttributeError: # empty field
                profile[tag] = 'unknown'
        irc.reply(("%(id)s (realname: %(realname)s) registered on %(registered)s; age: %(age)s / %(gender)s; "
                  "Country: %(country)s; Tracks played: %(playcount)s") % profile)

    profile = wrap(profile, [optional("something")])

    def compareUsers(self, irc, msg, args, user1, optionalUser2):
        """user1 [<user2>]

        Compares the taste from two users
        If <user2> is ommitted, the taste is compared against the ID of the calling user.
        """
        if not self.apiKey:
            irc.error("The API Key is not set for this plugin. Please set it via"
                      "config plugins.lastfm.apikey and reload the plugin. "
                      "You can sign up for an API Key using "
                      "http://www.last.fm/api/account/create", Raise=True)
        user2 = (optionalUser2 or self.db.getId(msg.nick) or msg.nick)

        channel = msg.args[0]
        maxResults = self.registryValue("maxResults", channel)
        # see http://www.lastfm.de/api/show/tasteometer.compare
        url = "%sapi_key=%s&method=tasteometer.compare&type1=user&type2=user&value1=%s&value2=%s&limit=%s" % (
            self.APIURL, self.apiKey, user1, user2, maxResults)
        try:
            f = utils.web.getUrlFd(url)
        except utils.web.Error as e:
            irc.error("Failure: %s" % (e), Raise=True)

        xml = minidom.parse(f)
        resultNode = xml.getElementsByTagName("result")[0]
        score = float(self._parse(resultNode, "score"))
        scoreStr = "%s (%s)" % (round(score, 2), self._formatRating(score))
        # Note: XPath would be really cool here...
        artists = [el for el in resultNode.getElementsByTagName("artist")]
        artistNames = [el.getElementsByTagName("name")[0].firstChild.data for el in artists]
        irc.reply("Result of comparison between %s and %s: score: %s, common artists: %s" \
                % (user1, user2, scoreStr, ", ".join(artistNames)))

    compare = wrap(compareUsers, ["something", optional("something")])

    def _parse(self, node, tagName, exceptMsg="not specified"):
            try:
                return node.getElementsByTagName(tagName)[0].firstChild.data
            except IndexError:
                return exceptMsg

    def _formatTimeago(self, unixtime):
        t = int(time()-unixtime)
        if t/86400 >= 1:
            return "%i days ago" % (t/86400)
        if t/3600 >= 1:
            return "%i hours ago" % (t/3600)
        if t/60 >= 1:
            return "%i minutes ago" % (t/60)
        if t > 0:
            return "%i seconds ago" % (t)

    def _formatRating(self, score):
        """<score>

        Formats <score> values to text. <score> should be a float
        between 0 and 1.
        """
        if score >= 0.9:
            return "Super"
        elif score >= 0.7:
            return "Very High"
        elif score >= 0.5:
            return "High"
        elif score >= 0.3:
            return "Medium"
        elif score >= 0.1:
            return "Low"
        else:
            return "Very Low"

dbfilename = conf.supybot.directories.data.dirize("LastFM.db")

Class = LastFM


# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
