# encoding: utf-8    
# vim: sts=4 sw=4 et
from evernote.edam.error.ttypes import EDAMUserException
from evernote.edam.limits import constants
from evernote.edam.notestore import NoteStore
from evernote.edam.notestore.ttypes import NoteFilter
from evernote.edam.type.ttypes import Tag
from evernote.edam.userstore import UserStore
from google.appengine.api import urlfetch, users
from google.appengine.ext import webapp, db
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import login_required, run_wsgi_app
from oauth import OAuthToken
from thrift.protocol import TBinaryProtocol
from thrift.transport import THttpClient
from xml.dom import minidom
from xml.sax.saxutils import escape
import datetime
import httplib
import logging
import oauth
import os
import re
import sys
import time
import traceback

sys.path.insert(0, os.path.abspath(os.path.dirname('./evernote')))
sys.path.insert(0, os.path.abspath(os.path.dirname('./evernote/edam')))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# FIXME
SERVER_HOST = 'localhost'
SERVER_PORT = 8080
SERVER_HOME = 'http://localhost:8080/'
# key and secret granted by the service provider for this consumer application - same as the MockOAuthDataStore
CONSUMER_KEY = 'your api key'
CONSUMER_SECRET = 'your secret'
DEBUG = False
# FIXME END
# 
# EVERNOTE CONSTANT URL
REQUEST_TOKEN_URL = 'https://www.evernote.com/oauth'
ACCESS_TOKEN_URL = 'https://www.evernote.com/oauth'
AUTHORIZATION_URL = 'https://www.evernote.com/OAuth.action'
RESOURCE_URL = 'http://www.evernote.com/edam/note/' 
# MY CONSTANT URL
CALLBACK_URL = SERVER_HOME + 'oauthcallback' 
NOTELIST_URL = SERVER_HOME + 'notelist' 
# MY CONSTANT うんたん
NICOEVER = u'★★★ edit by BUN☆TAN♪ ★★★ '
BUNTAN_TAG= 'buntan'

class EverNoteOAuthClient(oauth.OAuthClient):
    def __init__(self, server, port=httplib.HTTP_PORT, request_token_url='', access_token_url='', authorization_url=''):
        self.server = server
        self.port = port
        self.request_token_url = request_token_url
        self.access_token_url = access_token_url
        self.authorization_url = authorization_url
        self.connection = httplib.HTTPConnection("%s:%d" % (self.server, self.port))
  
    def fetch_request_token(self, oauth_request):
        # via headers
        # -> OAuthToken
        self.connection.request(oauth_request.http_method, self.request_token_url, headers=oauth_request.to_header()) 
        response = self.connection.getresponse()
        return oauth.OAuthToken.from_string(response.read())
  
    def fetch_access_token(self, oauth_request):
        # via headers
        # -> OAuthToken
        self.connection.request(oauth_request.http_method, self.access_token_url, headers=oauth_request.to_header()) 
        response = self.connection.getresponse()
        resstr = response.read()
        return oauth.OAuthToken.from_string(resstr)
  
    def authorize_token(self, oauth_request):
        # via url
        # -> typically just some okay response
        self.connection.request(oauth_request.http_method, oauth_request.to_url()) 
        response = self.connection.getresponse()
        return response.read()
  
    def access_resource(self, oauth_request):
        # via post body
        # -> some protected resources
        headers = {'Content-Type' :'application/x-www-form-urlencoded'}
        self.connection.request('POST', RESOURCE_URL, body=oauth_request.to_postdata(), headers=headers)
        response = self.connection.getresponse()
        return response.read()

class OAuth(webapp.RequestHandler):
    
    def __init__(self):
        self.client = EverNoteOAuthClient(SERVER_HOST, SERVER_PORT, REQUEST_TOKEN_URL, ACCESS_TOKEN_URL, AUTHORIZATION_URL)
        self.consumer = oauth.OAuthConsumer(CONSUMER_KEY, CONSUMER_SECRET)
        self.token = None
        pass
    
    def get(self , methodname):
        if DEBUG:
            logging.info(methodname)
            logging.info(self.request.arguments())
        try:
            method = getattr(self, methodname)
        except AttributeError:
            self.response.set_status(404)
            self.response.out.write("404 not found.")
            return
        method()
    
    @login_required
    def login(self): 
        """ evernoteの画面に転送し、ログインを行う
        """
        user = users.get_current_user()
        if self.checkloginned(user):
            self.redirect(NOTELIST_URL + "/index")
            return

        oauth_request = oauth.OAuthRequest.from_consumer_and_token(
                self.consumer,
                callback=CALLBACK_URL,
                http_url=self.client.request_token_url)
        oauth_request.sign_request(oauth.OAuthSignatureMethod_PLAINTEXT() , self.consumer, None)
        self.token = self.client.fetch_request_token(oauth_request)
        self.redirect(AUTHORIZATION_URL + "?oauth_token=%s&oauth_callback=%s" % (self.token.key, CALLBACK_URL))

    @login_required
    def logout(self): 
        """ logout """
        user = users.get_current_user()
        account = OAuth.getaccount(user)
        account.token_at = None
        account.put()
        
        message = u'ログアウトしました' 
        path = os.path.join(os.path.dirname(__file__), 'template/index.html')
        self.response.out.write(template.render(path, {'message':message,})) 
  
    @classmethod
    def getaccount(self, user):
        query = Account.all().filter("user =",  user)
        results = query.fetch(1)
        account = Account()
        for r in results:
            account = r
            
        return account
        
    @classmethod
    def checkloginned(self, user):
        # もし1日以内にトークンを獲得していた場合は、ログイン処理をSKIP
        account = OAuth.getaccount(user)
            
        if not account or not account.token_at:
            return False
            
        if account.token_at > (datetime.datetime.now() - datetime.timedelta(days=1)):
            return True
        return False

    def callback(self): 
        """ evernoteの画面から戻ってきた
        """
        self.token = OAuthToken(self.request.get('oauth_token'),None)
        oauth_request = oauth.OAuthRequest.from_consumer_and_token(self.consumer,
                                                                   token=self.token,
                                                                   verifier=None,
                                                                   http_url=self.client.access_token_url)
        oauth_request.sign_request(oauth.OAuthSignatureMethod_PLAINTEXT() , self.consumer, self.token)
        accesstoken = self.client.fetch_access_token(oauth_request)
        self.__accountsetup(accesstoken)
       
        self.redirect(NOTELIST_URL + "/index")
        
    def __accountsetup(self, accesstoken):
        user = users.get_current_user()
        query = Account.all().filter("user =",  user)
        results = query.fetch(1)
        account = None
        for r in results:
            account = r
            
        if account == None:
            account = Account()
            account.user = user
        
        account.token = accesstoken.key
        account.token_at = datetime.datetime.now()
        account.edam_shard = accesstoken.edam_shard
        account.defaulttag = BUNTAN_TAG
        account.put()
        
        return account
        
class MainPage(webapp.RequestHandler):
    """ indexページ """
    def get(self):
        """ main """
        user = users.get_current_user()
        if OAuth.checkloginned(user):
            self.redirect(NOTELIST_URL + "/index")
            return
        
        path = os.path.join(os.path.dirname(__file__), 'template/index.html')
        self.response.out.write(template.render(path, {})) 
        
class Riyokiyaku(webapp.RequestHandler):
    """ 規約ページ """
    def get(self):
        """ main """
        path = os.path.join(os.path.dirname(__file__), 'template/riyokiyaku.html')
        self.response.out.write(template.render(path, {})) 
       
class About(webapp.RequestHandler):
    """ aboutページ """
    def get(self):
        path = os.path.join(os.path.dirname(__file__), 'template/about.html')
        self.response.out.write(template.render(path, {})) 

class NoteBook(webapp.RequestHandler):
    """ 処理のメインがほぼ集まっている """
    def __getnotestore(self):
        iprot_note = THttpClient.THttpClient(RESOURCE_URL  + self.account.edam_shard)
        oprot_note = TBinaryProtocol.TBinaryProtocol(iprot_note)
        return NoteStore.Client(oprot_note)
        
    def post(self,methodname,subparam):
        self.__getpost(methodname, subparam)
        
    @login_required
    def get(self,methodname,subparam):
        self.__getpost(methodname, subparam)
        
    def __getpost(self,methodname,subparam):
        if DEBUG:
            logging.info('methodname=%s,subparam=%s' % (methodname, subparam))
        self.message = None
        try:
            method = getattr(self, methodname)
        except AttributeError:
            logging.error(traceback.format_exc(sys.exc_info()[2]))
            self.response.set_status(404)
            return
       
        if not hasattr(self, 'account'):
            if DEBUG:
                logging.info("account loaded...")
            self.account = self.__getaccount()
        try:
            method(subparam)
        except EDAMUserException,e:
            if e.errorCode == 9:
                # ログインできていないときは、未ログイン状態にする
                self.account.token_at = None
                self.account.put()
                self.redirect(SERVER_HOME)
            else:
                logging.error(sys.exc_info()[:2])
     
    
    def postmultinoteeditbytag(self,subparam):
        self.__postmulti()
        self.notebytag(self.account.fromtag)
        
    def postmultinoteeditbynotebook(self,subparam):
        self.__postmulti()
        self.notebynotebook(self.account.fromnotebook)
    
    def __postmulti(self):
        noteguids = self.request.get_all('editnote')
        for noteguid in noteguids:
            self.__editnotefromnico(noteguid)
        
        self.message = u'保存しました'
    
    def notebynotebook(self, subparam): 
        notestore = self.__getnotestore()
        filter = NoteFilter(notebookGuid=subparam)
        notebook = notestore.getNotebook(self.account.token, subparam)
        notes = notestore.findNotes(self.account.token, filter, 0, constants.EDAM_USER_NOTES_MAX )
        for note in notes.notes:
            # タイトルにニコニコ動画と入っていて、既に編集済みでないときはデフォルトチェック済み
            if DEBUG :
                logging.info('%s - findenico:%s ,tagfin:%s' % (note.title, note.title.decode('utf-8', 'ignore').find(u'ニコニコ動画'),(self.__checknotetagguids(note.tagGuids) )))
                                                                                                        
            if note.title.decode('utf-8').find(u'ニコニコ動画') != -1 and (self.__checknotetagguids(note.tagGuids) != True) :
                note.nico = 'checked'
            else:
                note.nico = ''
        
        template_values = { 
                           'notebook':notebook,
                           'notes':notes.notes,
                           }
        self.account.fromtag = None
        self.account.fromnotebook = subparam
        self.account.put()
        path = os.path.join(os.path.dirname(__file__), 'template/notesbynotebook.html')
        self.response.out.write(template.render(path, template_values)) 
        
    def __checknotetagguids(self, guids):
        if DEBUG:
            logging.info('guids:%s ,defaulttagguid:%s' % (guids,self.account.defaulttagguid))
            
        if guids is None:
            return False
        return self.account.defaulttagguid in guids

    def notebytag(self, subparam): 
        notestore = self.__getnotestore()
        filter = NoteFilter(tagGuids=[subparam])
        tag = notestore.getTag(self.account.token, subparam)
        notes = notestore.findNotes(self.account.token, filter, 0, constants.EDAM_USER_NOTES_MAX )
        for note in notes.notes:
            # タイトルにニコニコ動画と入っていて、既に編集済みでないときはデフォルトチェック済み
            if note.title.find('ニコニコ動画') != -1 and (self.__checknotetagguids(note.tagGuids) != True) :
                note.nico = 'checked'
            else:
                note.nico = ''
       
        template_values = { 
                           'tag':tag,
                           'notes':notes.notes,
                           }
        if DEBUG:
            logging.info(notes)
        self.account.fromtag = subparam
        self.account.fromnotebook = None
        self.account.put()
        path = os.path.join(os.path.dirname(__file__), 'template/notesbytag.html')
        self.response.out.write(template.render(path, template_values)) 
    
    def __searchtag(self,tags):        
        """ tagの中に、編集対象があるかちぇっくする"""
        for tag in tags:
            if tag.name.decode("utf-8", "ignore") == self.account.defaulttag.decode("utf-8", "ignore"):
                return tag
        return None

    def __editnotefromnico(self, noteguid):    
        notestore = self.__getnotestore()
        filter = NoteFilter(notebookGuid=noteguid)
        # tag (buntan)がない場合は、作成する。
        tags = notestore.listTags(self.account.token)
        tag = self.__searchtag(tags)
        if tag is None:
            newtag = Tag(name=self.account.defaulttag.encode('utf-8'))
            try:
                tag = notestore.createTag(self.account.token, newtag)
                self.account.defaulttagguid = tag.guid
            except Exception:
                # 例外発生時は無視
                logging.error(sys.exc_info()[:2])
                pass
        else:
            if self.account.defaulttagguid is None:
                self.account.defaulttagguid = tag.guid
                self.account.put()
        
        note = notestore.getNote(self.account.token, guid=noteguid, withContent=True,
                   withResourcesData=False,
                   withResourcesRecognition=False,
                   withResourcesAlternateData=False)
        
        nicoid = self.__nicourlcheck(note)
        
        if nicoid is not None:
            # niconicoのurlがある場合
            nicourl = 'http://www.nicovideo.jp/api/getthumbinfo/%s' % (nicoid)
            nicoapiresult = urlfetch.fetch(nicourl)
            dom = minidom.parseString(nicoapiresult.content)
            nicoinfo = self.__setnicoinfo(dom,nicourl)
            if note.tagGuids:
                note.tagGuids.append(self.account.defaulttagguid)
            else:
                note.tagGuids = [self.account.defaulttagguid]
            
            note.updated = int(time.time() * 1000)
            try:
                note.content = self.__setnotecontentadd(note, nicoinfo)
                note.contentLength = len(note.content)
                notestore.updateNote(self.account.token, note)
            except Exception:
                # 例外発生時は無視
                logging.error(sys.exc_info()[:2])
                logging.error(note.content)
                pass
        else:
            if DEBUG:
                logging.info('not nico match 保存できませんでした')
            self.message = '保存できませんでした'
            
    def __nicourlcheck(self,note):
        urlsource = note.content
        if note.attributes.sourceURL:
            urlsource = note.attributes.sourceURL
        else:
            # 普通にevernoteに記録すれば、sourceURLは保存されるので、此処に来ることはほとんどない。
            if DEBUG:
                logging.info('no sourceURL. %s' % note ) 
            pass
            
        search = urlsource.split('/')
            
        if len(search) != 1 :
            return search[-1] # 末尾を返す
        else:
            return None
        
    def nico2ever(self, subparam): 
        self.__editnotefromnico(subparam)
        self.notedetail(subparam)
            
    def __setnicoinfo(self, dom, nicourl): 
        nicoinfo = {}
        nicoinfo['nicourl'] = nicourl
        nodes = dom.getElementsByTagName("thumbnail_url")
        for node in nodes:
            nicoinfo['thubnail_url'] = node.childNodes[0].data
        nodes = dom.getElementsByTagName("description")
        for node in nodes:
            nicoinfo['description'] = escape(node.childNodes[0].data)
        nodes = dom.getElementsByTagName("title")
        for node in nodes:
            nicoinfo['title'] = node.childNodes[0].data
        nodes = dom.getElementsByTagName("watch_url")
        for node in nodes:
            nicoinfo['watch_url'] = node.childNodes[0].data
        return nicoinfo
        
    def __setnotecontentadd(self, note, nicoinfo ):
        content = note.content
        editcontent = content.decode('utf-8','ignore')
        oldcontent  = re.compile('<en-note>(.*)</en-note>', re.MULTILINE|re.DOTALL).search(editcontent)
        
        editcontent = '<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd"><en-note>'
        editcontent += self.__setnicoinfotable(note, nicoinfo)
        editcontent += '<p align="right">%s</p>' % NICOEVER
        if DEBUG:
            editcontent += datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z')
        # ここから下はオリジナルのノートを表示
        if oldcontent is not None and self.account.replacenote == False:
            editcontent += '<hr />'
            editcontent += oldcontent.group(1)
        editcontent += '</en-note>'
        
        editcontent = editcontent.encode('utf-8')
        return editcontent
   
    def __setnicoinfotable(self,note, nicoinfo):
        if DEBUG:
            logging.info(nicoinfo['nicourl'])
            logging.info(nicoinfo)
        table = '<table><tr bgcolor="%s"><th colspan=\'2\'><a href=\'%s\' target="_blank">%s</a></th></tr>' % ('Silver' ,nicoinfo['watch_url'],nicoinfo['title'])
        # ひとまずやらない: 保存した画像を使用する
        table += '<tr><td><a href=\'%s\' target="_blank"><img src=\'%s\' /></a></td>' % (nicoinfo['watch_url'],nicoinfo['thubnail_url']) 
        table += '<td><p><small>%s</small></p></td></tr></table>' % self.__setnicoinfodescriptionlink(nicoinfo['description'])
        return table
       
    def __setnicoinfodescriptionlink(self, description):
        """ urlやmylistをリンクに変更する """
        mylist = re.sub("(mylist\/\d*)","<a href='http://nicovideo.jp/\\1' target='_blank'>\\1</a>", description)
        smile = re.sub("(sm\d*)","<a href='http://nicovideo.jp/watch/\\1' target='_blank'>\\1</a>", mylist)
        return smile
        
    def posteditconfig(self, subparam): 
        """ 設定情報の更新 """
        self.account.adddefaulttag = (self.request.get('adddefaulttag') == 'on')
        self.account.defaulttag = self.request.get('defaulttag') 
        self.account.replacenote = (self.request.get('replacenote')  == 'on')
        
        self.account.put()
        self.message = '設定を更新しました。トップにもどるのは↑の画像をクリック。'
        
        self.config(subparam)
        
    def notedetail(self, subparam): 
        notestore = self.__getnotestore()
        filter = NoteFilter(notebookGuid=subparam)
        note = notestore.getNote(self.account.token, guid=subparam, withContent=True,
                   withResourcesData=False,
                   withResourcesRecognition=False,
                   withResourcesAlternateData=False)
        
        template_values = { 
                           'note':note,
                           'fromnotebook':self.account.fromnotebook,
                           'fromtag':self.account.fromtag,
                           'message':self.message, 
                           }
        if DEBUG:
            logging.info(self.account.adddefaulttag) 
            logging.info(note.resources)
        path = os.path.join(os.path.dirname(__file__), 'template/notedetail.html')
        self.response.out.write(template.render(path, template_values)) 
        

    def index(self, subparam=None): 
        iprot = THttpClient.THttpClient("https://www.evernote.com/edam/user")
        oprot = TBinaryProtocol.TBinaryProtocol(iprot)
        userstore = UserStore.Client(oprot)
        user = userstore.getUser(self.account.token)
        
        notestore = self.__getnotestore()
        notebooks = notestore.listNotebooks(self.account.token)
        tags = notestore.listTags(self.account.token)
        notebooks.sort(lambda x, y: cmp(x.name, y.name)) # name順にソート
        tags.sort(lambda x, y: cmp(x.name, y.name)) # name順にソート

        if self.account.defaulttag is None:
            self.account.defaulttag = BUNTAN_TAG
            
        self.account.fromtag = None
        self.account.fromnotebook = None
        self.account.put()
         
        template_values = { 
                           'notebooks':notebooks,
                           'tags':tags,
                           'user':user, 
                           }
        
        path = os.path.join(os.path.dirname(__file__), 'template/notebooks.html')
        self.response.out.write(template.render(path, template_values)) 
        
    def __getaccount(self):
        user = users.get_current_user()
        query = Account.all().filter("user =",  user)
        results = query.fetch(1)
        account = Account()
        for r in results:
            account = r
 
        return account

    def config(self, subparam): 
        template_values = { 
                           'message':self.message, 
                           'account':self.account, 
                           }
        path = os.path.join(os.path.dirname(__file__), 'template/config.html')
        self.response.out.write(template.render(path, template_values)) 
        
        
class Account(db.Model):
    user = db.UserProperty(auto_current_user=True)
    edam_shard = db.StringProperty(required=False)
    token = db.StringProperty(required=False)
    token_at = db.DateTimeProperty()
    fromnotebook = db.StringProperty(required=False)
    fromtag = db.StringProperty(required=False)
    defaulttag = db.StringProperty(required=False)
    defaulttagguid = db.StringProperty(required=False)
    # タグをnoteに追加する
    adddefaulttag = db.BooleanProperty(required=False,default=True)
    replacenote = db.BooleanProperty(required=False,default=False)

pagemapping = [('/', MainPage)
    , ('/riyokiyaku', Riyokiyaku)
    , ('/about', About)
    , ('/oauth(.*)', OAuth)
    , ('/notelist/(\w*)/?([a-zA-Z0-9-]*)', NoteBook)
    ]
application = webapp.WSGIApplication(pagemapping, debug=True)

def main():
    run_wsgi_app(application)

if __name__ == "__main__":
    main()

