import json, socket, threading, time, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys, os, tempfile
os.environ.setdefault("ASTOCK_HOME", tempfile.mkdtemp(prefix="astock-tests-"))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import web_app

class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),web_app.Handler);cls.port=cls.server.server_address[1]
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();time.sleep(.1)
        req=urllib.request.Request(f'http://127.0.0.1:{cls.port}/api/auth/register',data=json.dumps({"username":"tester","password":"password123"}).encode('utf-8'),headers={'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(req,timeout=3) as r:
            cls.cookie=(r.headers.get('Set-Cookie') or '').split(';',1)[0]
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close()
    def get(self,path,auth=True):
        headers={'Cookie':self.cookie} if auth else {}
        req=urllib.request.Request(f'http://127.0.0.1:{self.port}{path}',headers=headers)
        with urllib.request.urlopen(req,timeout=3) as r:return r.status,r.read()
    def post(self,path,payload,auth=True):
        headers={'Content-Type':'application/json'}
        if auth: headers['Cookie']=self.cookie
        req=urllib.request.Request(f'http://127.0.0.1:{self.port}{path}',data=json.dumps(payload).encode('utf-8'),headers=headers,method='POST')
        with urllib.request.urlopen(req,timeout=3) as r:return r.status,r.read()
    def test_health(self):
        status,body=self.get('/api/health');self.assertEqual(status,200);self.assertEqual(json.loads(body)['status'],'OK')
    def test_index(self):
        status,body=self.get('/');self.assertEqual(status,200);self.assertIn('A股智能投研网页版'.encode(),body)
    def test_dashboard_requires_auth(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get('/api/dashboard',auth=False)
        self.assertEqual(ctx.exception.code,401)
    def test_dashboard(self):
        status,body=self.get('/api/dashboard');self.assertEqual(status,200);self.assertIn('portfolio',json.loads(body))
    def test_recognize_positions_text(self):
        status,body=self.post('/api/positions/recognize',{'text':'600000 浦发银行 300 8.50 8.80'})
        data=json.loads(body)
        self.assertEqual(status,200)
        self.assertEqual(data['positions'][0]['symbol'],'600000')
        self.assertEqual(data['positions'][0]['quantity'],300)

if __name__=='__main__':unittest.main()
