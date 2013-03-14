import re
import urllib
import argparse
import os
import errno
import unicodedata
import getpass
from mechanize import Browser
from bs4 import BeautifulSoup

class CourseraDownloader(object):
    """
    Class to download content (videos, lecture notes, ...) from coursera.org for
    use offline.

    https://github.com/dgorissen/coursera-dl
    """

    BASE_URL =    'http://class.coursera.org/%s'
    HOME_URL =    BASE_URL + '/class/index'
    LECTURE_URL = BASE_URL + '/lecture/index'
    LOGIN_URL =   BASE_URL + '/auth/auth_redirector?type=login&subtype=normal'
    QUIZ_URL =    BASE_URL + '/quiz/index'
    ASSIGNMENT_URL = BASE_URL + '/assignment/index'

    DEFAULT_PARSER = "lxml"

    def __init__(self,username,password, quiz, parser=DEFAULT_PARSER):
        """Requires your coursera username and password. 
        You can also specify the parser to use (defaults to lxml), see http://www.crummy.com/software/BeautifulSoup/bs4/doc/#installing-a-parser
        """
        self.username = username
        self.password = password
        self.parser = parser
        self.quiz = quiz

        self.browser = Browser()
        self.browser.set_handle_robots(False)

    def login(self,course_name):
        print "* Authenticating as %s..." % self.username

        # open the course login page
        page = self.browser.open(self.LOGIN_URL % course_name)

        # check if we are already logged in by checking for a password field
        bs = BeautifulSoup(page,self.parser)
        pwdfield = bs.findAll("input",{"id":"password_login"})

        if pwdfield:
            self.browser.form = self.browser.forms().next()
            self.browser['email'] = self.username
            self.browser['password'] = self.password
            r = self.browser.submit()

            # check that authentication actually succeeded
            bs2 = BeautifulSoup(r.read(),self.parser)
            title = bs2.title.string
            if title.find("Login Failed") > 0:
                raise Exception("Failed to authenticate as %s" % (self.username,))
 
        else:
            # no login form, already logged in
            print "* Already logged in"


    def course_name_from_url(self,course_url):
        """Given the course URL, return the name, e.g., algo2012-p2"""
        return course_url.split('/')[3]

    def lecture_url_from_name(self,course_name):
        """Given the name of a course, return the video lecture url"""
        return self.LECTURE_URL % course_name

    def get_downloadable_content(self,course_url):
        """Given the video lecture URL of the course, return a list of all
        downloadable resources."""

        cname = self.course_name_from_url(course_url)

        print "* Collecting downloadable content from " + course_url

        # get the course name, and redirect to the course lecture page
        vidpage = self.browser.open(course_url)

        # extract the weekly classes
        soup = BeautifulSoup(vidpage,self.parser)
        headers = soup.findAll("div", { "class" : "course-item-list-header" })

        weeklyTopics = []
        allClasses = {}

        # for each weekly class
        for header in headers:
            h3 = header.findNext('h3')
            sanitisedHeaderName = sanitiseFileName(h3.text)
            weeklyTopics.append(sanitisedHeaderName)
            ul = header.next_sibling
            lis = ul.findAll('li')
            weekClasses = {}

            # for each lecture in a weekly class
            classNames = []
            for li in lis:
                className = sanitiseFileName(li.a.text)
                classNames.append(className)
                classResources = li.find('div', {'class':'course-lecture-item-resource'})

                hrefs = classResources.findAll('a')

                # for each resource of that lecture (slides, pdf, ...)
                # (dont set a filename here, that will be inferred from the headers)
                resourceLinks = [ (h['href'],None) for h in hrefs]
 
                # check if the video is included in the resources, if not, try
                # do download it directly
                hasvid = [x for x,_ in resourceLinks if x.find('.mp4') > 0]
                if not hasvid:
                    ll = li.find('a',{'class':'lecture-link'})
                    lurl = ll['data-modal-iframe']
                    p = self.browser.open(lurl)
                    bb = BeautifulSoup(p,self.parser)
                    vobj = bb.find('source',type="video/mp4")

                    if not vobj:
                        print " Warning: Failed to find video for %s" %  className
                    else:
                        vurl = vobj['src']
                        # build the matching filename
                        fn = className + ".mp4"
                        resourceLinks.append( (vurl,fn) )

                weekClasses[className] = resourceLinks

            # keep track of the list of classNames in the order they appear in the html
            weekClasses['classNames'] = classNames

            allClasses[sanitisedHeaderName] = weekClasses

        return (weeklyTopics, allClasses)

    def download(self, url, target_dir=".", target_fname=None):
        """Download the url to the given filename"""
        r = self.browser.open(url)

        # get the headers
        headers = r.info()

        # get the content length (if present)
        clen = int(headers['Content-Length']) if 'Content-Length' in headers else -1 
 
        # build the absolute path we are going to write to
        fname = target_fname or sanitiseFileName(CourseraDownloader.getFileName(headers)) or CourseraDownloader.getFileNameFromURL(url)
        filepath = os.path.join(target_dir,fname)

        dl = True
        if os.path.exists(filepath):
            if clen > 0: 
                fs = os.path.getsize(filepath)
                delta = clen - fs

                # all we know is that the current filesize may be shorter than it should be and the content length may be incorrect
                # overwrite the file if the reported content length is bigger than what we have already by at least k bytes (arbitrary)

                # TODO this is still not foolproof as the fundamental problem is that the content length cannot be trusted
                # so this really needs to be avoided and replaced by something else, eg., explicitly storing what downloaded correctly
                if delta > 2:
                    print '    - "%s" seems incomplete, downloading again' % fname
                else:
                    print '    - "%s" already exists, skipping' % fname
                    dl = False
            else:
                # missing or invalid content length
                # assume all is ok...
                dl = False

        try:
            if dl: self.browser.retrieve(url,filepath)
        except Exception as e:
            print "Failed to download url %s to %s: %s" % (url,filepath,e)

    def download_course(self,cname,dest_dir="."):
        """Download all the contents (quizzes, videos, lecture notes, ...) of the course to the given destination directory (defaults to .)"""

        # Ensure we are logged in
        self.login(cname)

        # get the lecture url
        course_url = self.lecture_url_from_name(cname)

        (weeklyTopics, allClasses) = self.get_downloadable_content(course_url)
        print '* Got all downloadable content for ' + cname

        course_dir = os.path.abspath(os.path.join(dest_dir,cname))

        # ensure the target dir exists
        if not os.path.exists(course_dir):
            os.mkdir(course_dir)

        print "* " + cname + " will be downloaded to " + course_dir

        # ensure the course directory exists
        if not os.path.exists(course_dir):
            os.makedirs(course_dir)

        # download the standard pages
        print " - Downloading lecture/syllabus pages"
        self.download(self.HOME_URL % cname,target_dir=course_dir,target_fname="index.html")
        self.download(course_url,target_dir=course_dir,target_fname="lectures.html")

        #search for wiki static pages in navigation and save
        p = self.browser.open(self.HOME_URL % cname)
        bs = BeautifulSoup(p,self.parser)

        qlist = bs.find('ul',{'class':'course-navbar-list'})
        qurls = [q['href'] for q in qlist.findAll('a')]
        qurls = [h for h in qurls if "page=" in h]        
        for url in qurls:
            filename = url.partition("/wiki/view?page=")[2]
            if not filename.endswith("html"):
                filename = filename + ".html"
                self.download(url,target_dir=course_dir,target_fname=filename)


        #download assignments
        self.download_assignments(cname,course_dir)
        
        # download the quizzes & homework if quiz flag is set in startup.
        
        if self.quiz:
            for qt in ['quiz','homework']:
                print "  - Downloading the '%s' quizzes" % qt
                try:
                    self.download_quizzes(cname,course_dir,quiz_type=qt)
                except Exception as e:
                   print "  - Failed %s" % e

        

        # now download the actual content (video's, lecture notes, ...)
        for j,weeklyTopic in enumerate(weeklyTopics,start=1):
            if weeklyTopic not in allClasses:
                #print 'Weekly topic not in all classes:', weeklyTopic
                continue

            # ensure the week dir exists
            # add a numeric prefix to the week directory name to ensure chronological ordering
            wkdirname = str(j).zfill(2) + " - " + weeklyTopic
            wkdir = os.path.join(course_dir,wkdirname)
            if not os.path.exists(wkdir):
                os.makedirs(wkdir)

            weekClasses = allClasses[weeklyTopic]
            classNames = weekClasses['classNames']

            print " - " + weeklyTopic

            for i,className in enumerate(classNames,start=1):
                if className not in weekClasses:
                    continue

                classResources = weekClasses[className]

                # ensure the class dir exists
                clsdirname = str(i).zfill(2) + " - " + className
                clsdir = os.path.join(wkdir,clsdirname)
                if not os.path.exists(clsdir): 
                    os.makedirs(clsdir)

                print "  - ources for " + className

                for classResource,tfname in classResources:
                    if not isValidURL(classResource):
                        absoluteURLGen = AbsoluteURLGen(course_url)
                        classResource = absoluteURLGen.get_absolute(classResource)
                        print "  -" + classResource, ' - is not a valid url'

                        if not isValidURL(classResource):
                            print "  -" + classResource, ' - is not a valid url'
                            continue

                    try:
                       #print '  - Downloading ', classResource
                       self.download(classResource,target_dir=clsdir,target_fname=tfname)
                    except Exception as e:
                       print "    - failed: ",classResource,e


    def download_quizzes(self,course,target_dir,quiz_type="quiz"):
        """Download each of the quizzes as separate html files, the quiz type is
        typically quiz or homework"""

        # extract the list of all quizzes
        qurl = (self.QUIZ_URL + "?quiz_type=" + quiz_type) % course
        p = self.browser.open(qurl)
        bs = BeautifulSoup(p,self.parser)

        qlist = bs.find('div',{'class':'course-item-list'})
        qurls = [q['href'].replace('/start?','/attempt?') for q in qlist.findAll('a',{'class':'btn-primary'})]
        titles = [t.string for t in qlist.find_all('h4')]
        
        #check to see if any URLs found before creating directory.
        if not qurls:
            return
        
        # ensure the target directory exists
        dir = os.path.join(target_dir,quiz_type)

        try:
            os.makedirs(dir)
        except OSError as e:
            if e.errno == errno.EEXIST:
                pass
            else: raise

        # download each quiz
        for i,it in enumerate(zip(qurls,titles),start=1):
            q,t = it
            fname = os.path.join(dir,str(i).zfill(2) + " - " + sanitiseFileName(t) + ".html")
            
            if os.path.exists(fname):
                pass
                #print "  - already exists, skipping"
            else:
                quiz = self.browser.open(q)
                bs = BeautifulSoup(quiz,self.parser)
                startform = bs.findAll("form", {"class":"course-quiz-start-form"})
                if startform:
                    print "Starting and Downloading Quiz"
                    self.browser.select_form(nr=0)
                    r = self.browser.submit()
                    content = r.read()
                    with open(fname, "w") as f:
                        f.write(content)
                    
                else:   
                    print "Downloading Quiz" 
                    self.browser.retrieve(q,fname)

    def download_assignments(self,course,target_dir):
        """Download each of the assignments as separate html files"""

        # extract the list of all assignments
        qurl = (self.ASSIGNMENT_URL) % course
        p = self.browser.open(qurl)
        bs = BeautifulSoup(p,self.parser)

        qlist = bs.find('div',{'class':'course-item-list'})
        qurls = [q['href'] for q in qlist.findAll('a',{'class':'btn-primary'})]
        titles = [t.string for t in qlist.find_all('h4')]
        
        #check to see if any URLs found before creating directory.
        if not qurls:
            return

        # ensure the target directory exists
        dir = os.path.join(target_dir,"assignments")

        try:
            os.makedirs(dir)
        except OSError as e:
            if e.errno == errno.EEXIST:
                pass
            else: raise

        # download each one
        for i,it in enumerate(zip(qurls,titles),start=1):
            q,t = it
            fname = os.path.join(dir,str(i).zfill(2) + " - " + sanitiseFileName(t) + ".html")
            
            if os.path.exists(fname):
                pass
                #print "  - already exists, skipping"
            else:
                #print "Found assignment" 
                self.browser.retrieve(q,fname)

    @staticmethod
    def extractFileName(contentDispositionString):
        #print contentDispositionString
        pattern = 'attachment; filename="(.*?)"'
        m = re.search(pattern, contentDispositionString)
        try:
            return m.group(1)
        except Exception:
            return ''

    @staticmethod
    def getFileName(header):
        try:
            return CourseraDownloader.extractFileName(header['Content-Disposition']).lstrip()
        except Exception:
            return '' 

    @staticmethod
    def getFileNameFromURL(url):
        splits = url.split('/')
        splits.reverse()
        splits = urllib.unquote(splits[0])
        #Seeing slash in the unquoted fragment
        splits = splits.split('/')
        fname = splits[len(splits) - 1]

        # add an extension if none
        ext = os.path.splitext(fname)[1]
        if not ext: fname += ".html"

        return sanitiseFileName(fname)

def sanitiseFileName(fileName):
    # ensure a clean, valid filename (arg may be both str and unicode)

    # ensure a unicode string, problematic ascii chars will get removed
    if isinstance(fileName,str):
        fn = unicode(fileName,errors='ignore')
    else:
        fn = fileName

    # normalize it
    fn = unicodedata.normalize('NFKD',fn)

    # encode it into ascii, again ignoring problematic chars
    s = fn.encode('ascii','ignore')

    # remove any characters not in the whitelist
    s = re.sub('[^\w\-\(\)\[\]\., ]','',s).strip()

    # ensure it is within a sane maximum
    max = 250

    # split off extension, trim, and re-add the extension
    fn,ext = os.path.splitext(s)
    s = fn[:max-len(ext)] + ext
    return s

def isValidURL(url):
    return url.startswith('http') or url.startswith('https')

def path2url(path):
    return urlparse.urljoin(
      'file:', urllib.pathname2url(path))

class AbsoluteURLGen(object):
    """
    Generate absolute URLs from relative ones
    Source: AbsoluteURLGen copy pasted from http://www.python-forum.org/pythonforum/viewtopic.php?f=5&t=12515
    """
    def __init__(self, base='', replace_base=False):
        self.replace_base = replace_base
        self.base_regex = re.compile('^(https?://)(.*)$')
        self.base = self.normalize_base(base)
   
    def normalize_base(self, url):
        base = url
        if self.base_regex.search(base):
            # rid thyself of 'http(s)://'
            base = self.base_regex.search(url).group(2)
            if not base.rfind('/') == -1:
                # keep only the directory, not the filename
                base = base[:base.rfind('/')+1]
            base = self.base_regex.search(url).group(1) + base
        return base

    def get_absolute(self, url=''):
        if not self.base or (
                self.replace_base and self.base_regex.search(url)):
            self.base = self.normalize_base(url)
            return url
        elif self.base_regex.search(url):
            # it's an absolute url, but we don't want to keep it's base
            return url
        else:
            # now, it's time to do some converting.
            if url.startswith("../"):
                # they want the parent dir
                if not self.base[:-2].rfind("/") == -1:
                    base = self.base[:self.base[:-2].rfind("/")+1]
                    return base + url[3:]
                else:
                    # there are no subdirs... broken link?
                    return url
            elif url.startswith("/"):
                # file is in the root dir
                protocol, base = self.base_regex.search(self.base).groups()
                # remove subdirs until we're left with the root
                while not base[:-2].rfind("/") == -1:
                    base = base[:base[:-2].rfind('/')]
                return protocol + base + url
            else:
                if url.startswith("./"):
                    url = url[2:]
                return self.base + url

# is lxml available?
def haslxml():
    try:
        import lxml
        return True
    except:
        return False

def main():
    # parse the commandline arguments
    parser = argparse.ArgumentParser(description='Download Coursera.org course videos/docs for offline use.')
    parser.add_argument("-u", dest='username', type=str, required=True, help='coursera.org username')
    parser.add_argument("-p", dest='password', type=str, help='coursera.org password')
    parser.add_argument("-d", dest='dest_dir', type=str, default=".", help='destination directory where everything will be saved')
    parser.add_argument("-q", dest='parser', type=str, default=CourseraDownloader.DEFAULT_PARSER,
                        help="the html parser to use, see http://www.crummy.com/software/BeautifulSoup/bs4/doc/#installing-a-parser")
    parser.add_argument("--quiz", dest='quiz', action="store_true", default=False, help="Allow downloading of quizzes. May trigger quiz attempts")
    parser.add_argument('course_names', nargs="+", metavar='<course name>',
                        type=str, help='one or more course names (from the url)')
    args = parser.parse_args()

    # check the parser
    parser = args.parser
    if parser == 'lxml' and not haslxml():
        print "Warning: lxml not available, falling back to built-in 'html.parser' (see -q option), this may cause problems on Python < 2.7.3"
        parser = 'html.parser'
    else:
        pass

    print "HTML parser set to %s" % parser

    # prompt the user for his password if not specified
    if not args.password:
        args.password = getpass.getpass()

    # instantiate the downloader class
    d = CourseraDownloader(args.username,args.password,args.quiz,parser=parser)

    # download the content
    for cn in args.course_names:
        d.download_course(cn,dest_dir=args.dest_dir)

if __name__ == '__main__':
    main()
