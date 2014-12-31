###
# Copyright (c) 2014, James Lu
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
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks

from collections import OrderedDict, defaultdict
try:  # Python 3
    from urllib.parse import urlencode, quote
except ImportError:  # Python 2
    from urllib import urlencode, quote
import json
try:
    from bs4 import BeautifulSoup
except ImportError:
    raise ImportError("Beautiful Soup 4 is required for this plugin: get it"
                      " at http://www.crummy.com/software/BeautifulSoup/bs4/"
                      "doc/#installing-beautiful-soup")

try:
    from supybot.i18n import PluginInternationalization
    _ = PluginInternationalization('PkgInfo')
except ImportError:
    # Placeholder that allows to run the plugin on a bot
    # without the i18n module
    _ = lambda x: x


class PkgInfo(callbacks.Plugin):
    """Fetches package information from the repositories of
    Debian, Arch Linux, Linux Mint, and Ubuntu."""
    threaded = True

    def __init__(self, irc):
        self.__parent = super(PkgInfo, self)
        self.__parent.__init__(irc)
        self.addrs = {'ubuntu': 'http://packages.ubuntu.com/',
                      'debian': "https://packages.debian.org/"}

    def _getDistro(self, release):
        """<release>

        Guesses the distribution from the release name."""
        release = release.lower()
        if release.startswith(("oldstable", "squeeze", "wheezy", "stable",
                               "jessie", "testing", "sid", "unstable")):
            distro = "debian"
        elif release.startswith(("hardy", "lucid", "maverick", "natty",
                                 "oneiric", "precise", "quantal", "raring",
                                 "saucy", "trusty", "utopic", "vivid")):
            distro = "ubuntu"
        else:
            distro = None
        return distro

    def MadisonParse(self, pkg, dist, codenames='', suite='', useSource=False,
                     reverse=False):
        """Parser for the madison API at https://qa.debian.org/madison.php."""
        # This arch value implies 'all' (architecture-independent packages)
        # and 'source' (source packages), in order to prevent misleading
        # "Not found" errors.
        arch = self.registryValue("archs") + ['all', 'source']
        arch = ','.join(set(arch))
        self.arg = {'package': pkg, 'table': dist, 'a': arch, 'c': codenames,
                    's': suite}
        if useSource:
            self.arg['S'] = 'on'
        self.arg = urlencode(self.arg)
        url = 'https://qa.debian.org/madison.php?text=on&' + self.arg
        self.log.debug("PkgInfo: Using url %s for 'vlist' command", url)
        d = OrderedDict()
        fd = utils.web.getUrlFd(url)
        for line in fd.readlines():
            L = line.decode("utf-8").split("|")
            L = map(str.strip, L)
            name, version, release, archs = L
            if useSource:
                d['%s: %s' % (release, name)] = (version, archs)
            else:
                d[release] = (version, archs)
        if d:
            if reverse:
                # *sigh*... I wish there was a better way to do this
                d = OrderedDict(reversed(tuple(d.items())))
            if self.registryValue("verbose"):
                items = ["{name} \x02({version} [{archs}])\x02".format(name=k,
                         version=v[0], archs=v[1]) for (k, v) in d.items()]
            else:
                items = ["{name} \x02({version})\x02".format(name=k,
                         version=v[0]) for (k, v) in d.items()]
            s = format('Found %n: %L', (len(d), 'result'), items)
            return s
        else:
            self.log.debug("PkgInfo: No results found for URL %s", url)

    def package(self, irc, msg, args, release, pkg, opts):
        """<release> <package> [--{depends|recommends|suggests}]

        Fetches information for <package> from Debian or Ubuntu's repositories.
        <release> is the codename/release name (e.g. 'trusty', 'squeeze'). If
        --depends, --recommends, or --suggests is given, fetches dependency
        info for <package>.
        For Arch Linux packages, please use 'archpkg' and 'archaur' instead."""
        pkg = pkg.lower()
        distro = self._getDistro(release)
        try:
            url = self.addrs[distro] + "{}/{}".format(release, pkg)
        except KeyError:
            irc.error('Unknown distribution.', Raise=True)
        try:
            fd = utils.web.getUrl(url).decode("utf-8")
        except utils.web.Error as e:
            irc.error(str(e), Raise=True)
        soup = BeautifulSoup(fd)
        if "Error" in soup.title.string:
            err = soup.find('div', attrs={"id": "content"}).find('p').string
            if "two or more packages specified" in err:
                irc.error("Unknown distribution/release.", Raise=True)
            irc.reply(err)
            return
        opts = dict(opts)
        if opts:
            items = soup.find_all('dt')
            keyws = {'depends': 'dep:', 'recommends': 'rec:',
                     'suggests': 'sug:'}
            if 'depends' in opts:
                lookup = 'depends'
            elif 'recommends' in opts:
                lookup = 'recommends'
            elif 'suggests' in opts:
                lookup = 'suggests'
            keyw = keyws[lookup]
            res = []
            for item in items:
                try:
                    name = item.a.text
                    if item.text.startswith("or") and keyw in \
                            item.find_previous_siblings("dt")[0].span.text:
                        res[-1] = "%s or \x02%s\x02" % (res[-1], name)
                    elif keyw in item.span.text:
                        res.append("\x02%s\x02" % name)
                except AttributeError as e:
                    continue
            if res:
                s = format("Package \x02%s\x02 %s: %L, View more at %u", pkg,
                           lookup, res, url)
                irc.reply(s)
            else:
                irc.error("%s doesn't seem to have any %s." % (pkg, lookup))
            return
        desc = soup.find('meta', attrs={"name": "Description"})["content"]
        # Get package information from the meta tags
        keywords = soup.find('meta', attrs={"name": "Keywords"})["content"]
        keywords = keywords.replace(",", "").split()
        version = keywords[-1]
        if version == "virtual":
            providing = [obj.a.text for obj in soup.find_all('dt')]
            desc = ("Virtual package provided by: \x02%s\x02" %
                    ', '.join(providing[:10]))
            if len(providing) > 10:
                desc += " and \x02%s\x02 others" % (len(providing) - 10)
        s = format("Package: \x02%s (%s)\x02 in %s - %s, View more at: %u",
                   pkg, version, keywords[1], desc, url)
        irc.reply(s)
    pkg = wrap(package, ['somethingWithoutSpaces', 'somethingWithoutSpaces',
               getopts({'depends': '', 'recommends': '', 'suggests': ''})])

    def vlist(self, irc, msg, args, distro, pkg, opts):
        """<distribution> <package> [--source] [--reverse]

        Fetches all available version of <package> in <distribution>, if
        such package exists. Supported entries for <distribution>
        include 'debian', 'ubuntu', 'derivatives', and 'all'. If
        --source is given, search for packages by source package
        name. If --reverse is given, show the newest package versions
        first."""
        pkg, distro = map(str.lower, (pkg, distro))
        supported = ("debian", "ubuntu", "derivatives", "all")
        if distro not in supported:
            distro = self._getDistro(distro)
            if distro is None:
                irc.error("Unknown distribution.", Raise=True)
        opts = dict(opts)
        reverse = 'reverse' in opts
        d = self.MadisonParse(pkg, distro, useSource='source' in opts,
                              reverse=reverse)
        if not d:
            irc.error("No results found.", Raise=True)
        try:
            url = "{}search?keywords={}".format(self.addrs[distro], pkg)
            d += format(" View more at: %u", url)
        except KeyError:
            pass
        irc.reply(d)
    vlist = wrap(vlist, ['somethingWithoutSpaces', 'somethingWithoutSpaces',
                 getopts({'source': '', 'reverse': ''})])

    def archpkg(self, irc, msg, args, pkg, opts):
        """<package> [--exact]

        Looks up <package> in the Arch Linux package repositories.
        If --exact is given, will output only exact matches.
        """
        pkg = pkg.lower()
        baseurl = 'https://www.archlinux.org/packages/search/json/?'
        if 'exact' in dict(opts):
            url = baseurl + urlencode({'name': pkg})
        else:
            url = baseurl + urlencode({'q': pkg})
        self.log.debug("PkgInfo: using url %s for 'archpkg' command", url)
        fd = utils.web.getUrl(url)
        data = json.loads(fd.decode("utf-8"))
        if data['valid'] and data['results']:
            # We want one entry per package, but the API gives one
            # entry per architecture! Remove duplicates with a set:
            results = set()
            archs = defaultdict(list)
            for x in data['results']:
                s = "\x02{name}\x02 - {desc} \x02({version})\x02".format(
                    name=x['pkgname'], desc=x['pkgdesc'], version=x['pkgver'])
                results.add(s)
                archs[s].append(x['arch'])
            count = len(results)
            items = [format("%s \x02[%s]\x02", s, ', '.join(archs[s])) for s
                     in results]
            irc.reply(format('Found %n: %L', (len(results), 'result'),
                             list(results)))
        else:
            irc.error("No results found.", Raise=True)
    archpkg = wrap(archpkg, ['somethingWithoutSpaces', getopts({'exact': ''})])

    def archaur(self, irc, msg, args, pkg):
        """<package>

        Looks up <package> in the Arch Linux AUR."""
        pkg = pkg.lower()
        baseurl = 'https://aur.archlinux.org/rpc.php?type=search&'
        url = baseurl + urlencode({'arg': pkg})
        self.log.debug("PkgInfo: using url %s for 'archaur' command", url)
        fd = utils.web.getUrl(url)
        data = json.loads(fd.decode("utf-8"))
        if data["type"] == "error":
            irc.error(data["results"], Raise=True)
        count = data["resultcount"]
        if count:
            # We want this to be limited to prevent overflow warnings
            # in the bot.
            if count > 150:
                count = '150+'
            s = format("Found %n: ", (data["resultcount"], 'result'))
            for x in data['results'][:150]:
                verboseInfo = ''
                if self.registryValue("verbose"):
                    verboseInfo = format("[ID: %s Votes: %s]", x['ID'],
                                         x['NumVotes'])
                s += "{name} - {desc} \x02({version} {verbose})\x02, " \
                    .format(name=x['Name'], desc=x['Description'],
                            version=x['Version'], verbose=verboseInfo)
            irc.reply(s[:-2])  # cut off the ", " at the end
        else:
            irc.error("No results found.", Raise=True)
    archaur = wrap(archaur, ['somethingWithoutSpaces'])

    def pkgsearch(self, irc, msg, args, distro, query):
        """<distro> <query>

        Looks up <query> in <distro>'s website (for Debian/Ubuntu)."""
        distro = distro.lower()
        if distro not in ("debian", "ubuntu"):
            distro = self._getDistro(distro)
        try:
            url = '%ssearch?keywords=%s' % (self.addrs[distro], quote(query))
        except KeyError:
            irc.error('Unknown distribution.', Raise=True)
        try:
            fd = utils.web.getUrl(url).decode("utf-8")
        except utils.web.Error as e:
            irc.error(str(e), Raise=True)
        soup = BeautifulSoup(fd)
        # Debian/Ubuntu use h3 for result names in the format 'Package abcd'
        results = [pkg.string.split()[1] for pkg in soup.find_all('h3')]
        if results:
            s = format("Found %n: \x02%L\x02, View more at: %u",
                       (len(results), 'result'), results, url)
            irc.reply(s)
        else:
            try:
                # Look for "too many results" errors and others reported by the
                # web interface.
                if distro == "debian":
                    errorParse = soup.find("div", class_="note").p
                else:
                    errorParse = soup.find("p", attrs={"id":
                                                       "psearchtoomanyhits"})
                if errorParse:
                    for br in errorParse.findAll('br'):
                        br.replace_with(" ")
                    e = errorParse.text.strip()
            except AttributeError:
                e = "No results found."
            irc.error(e)
    pkgsearch = wrap(pkgsearch, ['somethingWithoutSpaces',
                                 'somethingWithoutSpaces'])

    def mintpkg(self, irc, msg, args, release, query, opts):
        """<release> <package> [--exact]

        Looks up <package> in Linux Mint's repositories. If --exact is given,
        look up packages by the exact package name. Otherwise, look it up
        as a simple glob pattern."""
        addr = 'http://packages.linuxmint.com/list.php?release=' + \
            quote(release)
        try:
            fd = utils.web.getUrl(addr).decode("utf-8")
        except utils.web.Error as e:
            irc.error(str(e), Raise=True)
        soup = BeautifulSoup(fd)
        # Linux Mint puts their package lists in tables
        results = soup.find_all("td")
        found = OrderedDict()
        query = query.lower()
        exact = 'exact' in dict(opts)
        for result in results:
            name = result.contents[0].string  # Package name
            if query == name or (query in name and not exact):
                # This feels like really messy code, but we have to find tags
                # relative to our results.
                # Ascend to find the section name (in <h2>):
                section = result.parent.parent.parent.previous_sibling.\
                    previous_sibling.string
                # Find the package version in the next <td>; for some reason we
                # have to go two siblings further, as the first .next_sibling
                # returns '\n'. This is mentioned briefly in Beautiful Soup 4's
                # documentation...
                version = result.next_sibling.next_sibling.string
                # We format our found dictionary this way because the same
                # package can exist multiple times in different sections of
                # the repository (e.g. one in Main, one in Backports, etc.)
                found['%s [\x02%s\x02]' % (name, section)] = version
        if found:
            items = [format('%s \x02(%s)\x02', pkg, found[pkg]) for pkg in
                     found]
            s = format('Found %n: %L, %s %u', (len(found), 'result'), items,
                       _('View more at: '), addr)
            irc.reply(s)
        else:
            irc.error('No results found.')
    mintpkg = wrap(mintpkg, ['somethingWithoutSpaces',
                             'somethingWithoutSpaces',
                             getopts({'exact': ''})])

Class = PkgInfo


# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
