import os, stat, sys, random, urllib2, shutil, zipfile, subprocess
from time import sleep
from subprocess import Popen, PIPE
from PIL import Image

import omg
import pytumblr
import tweepy
from BeautifulSoup import BeautifulSoup
from iwad_maps import doom1_maps, doom2_maps, tnt_maps, plutonia_maps, heretic_maps, hexen_maps

# WADBOT v2
# random screenshot from a random wad

'''
TODO:
- scan wad for missing texture refs with omgifol, see if missing textures are available in commonly used texture packs eg gothic, community chest, final doom, hacx, etc
- handle WAD/UDMF map lumps in PK3 files
- add unique texture count in view to score
-- see line ~151 of gl/scene/gl_bsp.cpp, grab texture refs (low/mid/hi) from line, store in a list of textures seen in this scene, if texture not already in list add and rendered_textures++
-- score non-vanilla (IWAD) lumps double
-- thread: http://forum.zdoom.org/viewtopic.php?f=15&t=53099
- finish heretic/hexen author credits: http://www.doomworld.com/vb/doom-general/67597-original-iwad-author-credits
- possible bug w/ allcaps WAD extension?

TODO v3:
- use idgames REST API: http://www.doomworld.com/idgames/api/
- keep full, up to date local mirror of idgames archive!
- finish DoomMapFromDir functionality, crawl dirs and pick a zip/wad
- build database of maps from full idgames archive + IWADs, choice() from that
-- how to get the idgames URL for a locally archived file? filename search? regex?
'''

'''
dependencies:
beautifulsoup: http://www.crummy.com/software/BeautifulSoup
omgifol: https://bitbucket.org/alexmax2742/omgifol (other repos are busted)
xte: sudo apt-get install xautomation
GZDoom "goodshot" branch: https://github.com/JPLeBreton/gzdoom.git
pytumblr: https://github.com/tumblr/pytumblr
'''

# don't actually go through with the tumblr post submit
CAN_POST = True

MAX_POST_ATTEMPTS = 10

#
# part 1: download a random file from the idgames/ archive, find a map to open
#

RANDOM_FILE_URL = 'http://doomworld.com/idgames/?random'
DOWNLOAD_URL_PATTERN = 'http://ftp.mancubus.net/pub/idgames/'
AUTHOR_LINE_PATTERN = 'Author:'
DESC_LINE_PATTERN = 'Description:'
DATE_LINE_PATTERN = 'Date:'
# idgames dir patterns we don't care about, eg utils
SKIP_DIR_PATTERNS = ['utils/', 'sounds/']
URL_REQ_HEADER = { 'User-Agent' : 'Mozilla/4.0 (compatible; MSIE 5.5; Windows NT)' }
ZIP_EXTRACT_DIR = './wad'
TEMP_ZIP_FILENAME = 'wad.zip'
LOAD_FILE_EXTS = ['wad', 'pk3', 'deh']

#
# part 2: launch the map in gzdoom, take a shot at a random DM spawn point, quit
#

SH_PATH = '/bin/sh'
GEN_SCRIPT_PATH = './launch.sh'
GZDOOM_PATH = '../gzdoom_goodshot/release/gzdoom'
# INI with good screenshot-friendly config
INI_PATH = './zdoom.ini'
IWAD_PATH = '~/game/doom/iwad'
ALWAYS_INCLUDE_PWADS = ['nakdplyr.wad']
LOG_PATH = './wadbot2.log'
LOG_MAP_SEP = '<------------------------------->'
LOG_LOC_STRING = 'Current player position:'
SCREENSHOT_FILENAMES = ['shot0.gif']
SCREENSHOT_OUTPUT_FILENAMES = ['shot0.gif']
GOODSHOT_KEY = 'Z'
PRE_SHOT_WAIT_SECONDS = 3

#
# part 3: get some more post data from the log file, post shot + text draft to tumblr
#

AUTH_DATA_FILE = 'tumblr_oauth.py'

def is_iwad_name(full_map_name):
    map_tables = [doom1_maps, doom2_maps, heretic_maps, hexen_maps]
    for table in map_tables:
        for name in table.keys():
            if full_map_name.lower() == name.lower():
                return True
    return False

def get_final_doom_name(full_map_name, iwad_names):
    mapid = full_map_name[:full_map_name.find(' ')]
    for name in iwad_names:
        if name[:name.find(' ')] == mapid:
            return name
    return 'ERROR - final doom map name not found'

class DoomMap:
    "base class, common code for local vs web get"
    # link to idgames/ page
    page_link = None
    file_link = None
    author_name = ''
    description = ''
    file_date = ''
    # files in zip to feed gzdoom
    files_to_load = []
    # name of wad file this map lives in
    map_wad = ''
    # level #(s) to feed -warp switch
    level = ''
    # iwad (just doom.wad & doom2.wad for now)
    iwad = ''
    # map's full name, as displayed on intermission screen etc
    full_name = ''
    # XYZ location of screenshot taken
    loc = ''
    
    def set_info_from_wad_page(self, wad_page_soup):
        "sets some data from BeautifulSoup wad page object, returns success"
        # find file download link
        self.file_link = None
        for link in wad_page_soup.findAll('a'):
            if link.get('href') and link.get('href').startswith(DOWNLOAD_URL_PATTERN):
                self.file_link = link.get('href')
                break
        if not self.file_link:
            print("Couldn't find a DL link on page %s" % wad_page_soup.title.text)
            return False
        # get wad author, description, and date from lines on the page
        def get_cell_text(page, pattern):
            cells = page.findAll('td')
            for i,cell in enumerate(cells):
                if cell.text == pattern:
                    return cells[i+1].text
        self.author_name = get_cell_text(wad_page_soup, AUTHOR_LINE_PATTERN)
        self.description = get_cell_text(wad_page_soup, DESC_LINE_PATTERN)
        self.file_date = get_cell_text(wad_page_soup, DATE_LINE_PATTERN)
        for pattern in SKIP_DIR_PATTERNS:
            if self.file_link.startswith(DOWNLOAD_URL_PATTERN + pattern):
                print("Link isn't a level")
                return False
        return True
    
    def get_random_map_shot(self):
        # search any WAD files for maps using omgifol
        self.all_wad_maps = []
        # remember which file a map lives in (for multi-wad zips)
        map_wad_mapping = {}
        for f in self.files_to_load:
            if f.lower().endswith('wad'):
                maps = omg.WAD(f).maps.keys()
                if not maps:
                    continue
                for m in maps:
                    # don't add MAPINFO lumps
                    if m != 'MAPINFO':
                        self.all_wad_maps.append(m)
                        map_wad_mapping[m] = f
        if len(self.all_wad_maps) > 0:
            print('Maps found: %s' % ', '.join(self.all_wad_maps))
        else:
            print("Couldn't find any maps")
            return
        # pick a map
        map_name = random.choice(self.all_wad_maps)
        self.map_wad = os.path.basename(map_wad_mapping[map_name])
        # get correct number(s) to feed -warp CLI switch: map# for doom2,
        # E# M# for doom1
        if map_name.startswith('E'):
            self.level = '%s %s' % (map_name[1], map_name[3:])
        elif map_name.startswith('MAP'):
            self.level = map_name[3:]
        else:
            # something else, gzdoom hopefully opens these by lump name
            self.level = map_name
        # determine IWAD to launch with
        # two numbers = doom1 eXmY map, one number = doom2 mapXX format
        # if already set, we got it from a url, eg heretic or hexen
        if self.file_link and self.file_link.startswith(DOWNLOAD_URL_PATTERN + 'levels/heretic/'):
            self.iwad = 'heretic.wad'
        elif self.file_link and self.file_link.startswith(DOWNLOAD_URL_PATTERN + 'levels/hexen/'):
            self.iwad = 'hexen.wad'
        elif self.map_wad.lower() in ['heretic.wad', 'hexen.wad', 'hexdd.wad']:
            self.iwad = self.map_wad
        elif ' ' in self.level:
            self.iwad = 'doom.wad'
        else:
            self.iwad = 'doom2.wad'
        print('Will try to load map %s' % self.level)
        # enclose pwad file names in quotes
        file_list = []
        for f in ALWAYS_INCLUDE_PWADS + self.files_to_load:
            file_list.append('"%s"' % f)
        # generate a shell script to run gzdoom with all the correct settings
        cmd = ['python2', 'dmvis.py', ' '.join(self.files_to_load), map_name]
        print(' '.join(cmd))
        subprocess.call(cmd)

        self.shot_valid = os.path.exists(SCREENSHOT_FILENAMES[0]) # and os.path.exists(LOG_PATH)
        if self.shot_valid:
            self.post()
    
    def open_zip(self, zip_filename):
        "read the file as a zip archive, return success"
        try:
            zip_data = zipfile.ZipFile(zip_filename)
        except:
            print("Couldn't load zip data from file %s" % file_link)
            return
        zip_data.extractall(ZIP_EXTRACT_DIR)
        extracted_files = os.listdir(ZIP_EXTRACT_DIR)
        self.files_to_load = []
        # see if any load-able files are in the extracted zip
        for f in extracted_files:
            for ext in LOAD_FILE_EXTS:
                if f.lower().endswith(ext):
                    self.files_to_load.append('%s/%s' % (ZIP_EXTRACT_DIR, f))
        if len(self.files_to_load) == 0:
            print("Couldn't find any usable files in extracted zip")
            return
        else:
            print('Examining files: %s' % (', '.join(self.files_to_load)))
        self.get_random_map_shot()
    
    def post(self):
        # self.full_name = self.map_wad
        # comb log file for map name and player location
        # log_lines = open(LOG_PATH).readlines()
        # for i,line in enumerate(log_lines):
        #     if LOG_MAP_SEP in line:
        #         # remove line break after map name line
        #         self.full_name = log_lines[i+2].strip()
        #     elif LOG_LOC_STRING in line:
        #         left_paren_idx = line.find('(')
        #         right_paren_idx = line.find(')')
        #         self.loc = line[left_paren_idx+1 : right_paren_idx].split(',')
        #         self.loc = ', '.join(self.loc)
        # if page_link is missing, omit it from post
        if self.page_link:
            post = '<a href="%s">%s</a>' % (self.page_link, self.map_wad)
        else:
            post = '%s' % self.map_wad
        # if map is from an IWAD, use its name as-is
        if self.map_wad.lower() == 'doom.wad':
            post += self.full_name
            self.author_name = doom1_maps[self.full_name]
        elif self.map_wad.lower() == 'doom2.wad':
            post += self.full_name
            self.author_name = doom2_maps[self.full_name]
        # look up unique names for tnt and plutonia maps that don't appear
        # in log
        elif self.map_wad.lower() == 'tnt.wad':
            self.full_name = get_final_doom_name(self.full_name, tnt_maps)
            post += self.full_name
            self.author_name = tnt_maps[self.full_name]
        elif self.map_wad.lower() == 'plutonia.wad':
            self.full_name = get_final_doom_name(self.full_name, plutonia_maps)
            post += self.full_name
            self.author_name = plutonia_maps[self.full_name]
        elif is_iwad_name(self.full_name) and self.map_wad.lower() != self.iwad:
            # if map doesn't have a name, ie is in a PWAD and shares name with
            # IWAD map in that slot, just show the wad name and the base map
            # name eg MAP01 or E1M1
            self.full_name = self.full_name[:self.full_name.find('-')-1]
            post += self.full_name
        elif self.full_name:
            post += self.full_name
        else:
            print("Couldn't find map name in log")
        tweet = self.map_wad
        # tweet = '%s, %s' % (self.map_wad, self.full_name)
        # post += ' (%s)' % self.loc
        if self.author_name != '':
            post += '\n<br/>Author: %s' % self.author_name
        if self.file_date != '':
            post += '\n<br/>Date: %s' % self.file_date
        if self.description != '':
            post += '\n<br/>Description:\n<br/>%s' % self.description
        post = unicode(post)
        print('------------\nfinal post:')
        print(post)
        # compress PNG image to JPG
        # for i,shot_filename in enumerate(SCREENSHOT_FILENAMES):
        #     img = Image.open(shot_filename)
        #     img.save(SCREENSHOT_OUTPUT_FILENAMES[i])
        if not CAN_POST:
            return
        # use tumblr API to submit draft post of shot.png + post_string
        exec(open(AUTH_DATA_FILE).read())
        client = pytumblr.TumblrRestClient(consumer_key, consumer_secret,
                                           oauth_token, oauth_secret)
        twitter_auth = tweepy.OAuthHandler(twitter_consumer_key, twitter_consumer_secret)
        twitter_auth.set_access_token(twitter_access_token, twitter_access_token_secret)
        twitter_api = tweepy.API(twitter_auth)
        try:
            client.create_photo('dmvisbot.tumblr.com', state="published", tags=["doom", "wadbot"],
                                data=SCREENSHOT_OUTPUT_FILENAMES, caption=post)
            print("Tumblr success!")
            tumblr_draft = client.posts('dmvisbot.tumblr.com')["posts"][0]
            tweet += ' %s' % tumblr_draft["post_url"]
            try:
                twitter_api.update_with_media(SCREENSHOT_OUTPUT_FILENAMES[0], status=tweet)
                # client.edit_post('dmvisbot.tumblr.com', id=tumblr_draft["id"], type="photo", state="published")
                self.post_valid = True
                print("Tweet success!")
            except Exception, e:
                print(str(e))
                print("Couldn't tweet!  Bad OAuth or no net connection?")
        except:
            print("Couldn't post!  Bad OAuth or no net connection?")

class DoomMapFromWeb(DoomMap):
    
    def __init__(self, arg):
        "a Map object with all data needed to take a screenshot"
        self.shot_valid = False
        self.post_valid = False
        # get soup object for a zip page
        req = urllib2.Request(RANDOM_FILE_URL, None, URL_REQ_HEADER)
        try:
            page = urllib2.urlopen(req)
        except:
            print("Couldn't open random file link")
            return
        # geturl gives page link ?random took us to
        self.page_link = page.geturl()
        wad_page = BeautifulSoup(page.read())
        wad_page_success = self.set_info_from_wad_page(wad_page)
        if not wad_page_success:
            return
        print('Downloading file from link %s' % self.file_link)
        # wipe out the old dir first
        if os.path.exists(ZIP_EXTRACT_DIR):
            shutil.rmtree(ZIP_EXTRACT_DIR)
        os.mkdir(ZIP_EXTRACT_DIR)
        # download the file
        zip_file = open(TEMP_ZIP_FILENAME, 'wb')
        req = urllib2.Request(self.file_link, None, URL_REQ_HEADER)
        zip_file.write(urllib2.urlopen(req).read())
        zip_file.close()
        self.open_zip(TEMP_ZIP_FILENAME)


class DoomMapFromDir(DoomMap):
    
    def __init__(self, dirname):
        self.shot_valid = False
        for dirname, dirnames, filenames in os.walk('.'):
            if '.git' in dirnames:
                dirnames.remove('.git')
        # TODO: random filename using walk() in eg archive dir
        # detect whether chosen dir has zips or wads, return/replace self w/
        # respective class?

class DoomMapFromZip(DoomMap):
    
    def __init__(self, filename):
        self.shot_valid = False
        # TODO: get self.page_link and wad page data to pass to set_info_from_wad_page
        self.open_zip(filename)

class DoomMapFromWad(DoomMap):
    
    def __init__(self, filename):
        self.shot_valid = False
        self.post_valid = False
        print('Examining file: %s' % filename)
        self.files_to_load = [filename]
        self.get_random_map_shot()

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] != '':
        arg = sys.argv[1]
        if os.path.isdir(arg):
            print('Fetching from directory %s' % arg)
            map_class = DoomMapFromDir
        elif arg.lower().endswith('wad'):
            print('Fetching from WAD file %s' % arg)
            map_class = DoomMapFromWad
        elif arg.lower().endswith('zip'):
            print('Fetching from ZIP file %s' % arg)
            map_class = DoomMapFromZip
        else:
            print("Couldn't fetch from %s" % arg)
            sys.exit()
    else:
        print('No args given, fetching from web...')
        map_class = DoomMapFromWeb
        arg = None
    if os.path.isfile(SCREENSHOT_FILENAMES[0]):
        os.remove(SCREENSHOT_FILENAMES[0])
    m = map_class(arg)
    attempts = 0
    while ((not m.shot_valid) or (not m.post_valid)) and (attempts < MAX_POST_ATTEMPTS):
        m = map_class(arg)
        attempts += 1
        if attempts == MAX_POST_ATTEMPTS:
            print('Failed after %s attempts, giving up!' % attempts)
        # elif m.shot_valid and (not m.post_valid):
        #     print('Attempting post...')
        #     m.post()
    print("dmvisbot2.py out!")
