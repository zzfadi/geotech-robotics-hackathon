import time,base64,json,tempfile,threading,unittest,urllib.request,urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer
from debug_server import Handler,Store

class ServerTest(unittest.TestCase):
    def test_queue_auth_and_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.store=Store(Path(directory))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            url='http://127.0.0.1:'+str(server.server_port)
            def request(path,data=None,token=None,origin=None):
                headers={'Content-Type':'application/json'}
                if token:headers['Authorization']='Bearer '+token
                if origin:headers['Origin']=origin
                req=urllib.request.Request(url+path,data=None if data is None else json.dumps(data).encode(),headers=headers)
                with urllib.request.urlopen(req) as r:return json.load(r)
            try:
                with self.assertRaises(urllib.error.HTTPError) as e:request('/api/worker/next')
                self.assertEqual(e.exception.code,401)
                request('/api/worker/next',token=server.store.token)
                with self.assertRaises(urllib.error.HTTPError) as e:request('/api/command',{'prompt':'move'},origin='https://unrelated.example')
                self.assertEqual(e.exception.code,403)
                command=request('/api/command',{'prompt':'Move toward cube'})
                with self.assertRaises(urllib.error.HTTPError) as e:request('/api/command',{'prompt':'duplicate'})
                self.assertEqual(e.exception.code,409)
                claimed=request('/api/worker/next',token=server.store.token)['command']
                self.assertEqual(claimed['id'],command['id']);self.assertEqual(claimed['status'],'running')
                frame={'request_id':command['id'],'sequence':1,'captured_unix':time.time(),'frames':{k:base64.b64encode(b'\xff\xd8test\xff\xd9').decode() for k in ('video.overhead','video.wrist_l','video.wrist_r')}}
                with self.assertRaises(urllib.error.HTTPError) as e:request('/api/worker/frame',frame)
                self.assertEqual(e.exception.code,401)
                request('/api/worker/frame',frame,server.store.token)
                self.assertEqual(request('/api/live')['frame']['sequence'],1)
                self.assertEqual(request('/api/replay/0')['frame']['request_id'],command['id'])
                self.assertNotIn('captured_unix',(Path(directory)/'events.jsonl').read_text())
                stale=dict(frame,captured_unix=time.time()-20)
                with self.assertRaises(urllib.error.HTTPError) as e:request('/api/worker/frame',stale,server.store.token)
                self.assertEqual(e.exception.code,400)
                request('/api/stop',{})
                self.assertTrue(request('/api/status')['command']['stop'])
                event=request('/api/events',{'kind':'test','api_key':'MUST_NOT_STORE'},server.store.token)
                self.assertNotIn('api_key',event)
                self.assertNotIn(server.store.token,json.dumps(request('/api/status')))
                voice=request('/api/voice',{'audio':base64.b64encode(b'x'*200).decode()})
                public=request('/api/status')['voice']
                self.assertNotIn('audio',public)
                self.assertIn('audio',request('/api/worker/next',token=server.store.token)['voice'])
                with self.assertRaises(urllib.error.HTTPError) as e:request('/api/worker/transcript',{'id':voice['id'],'text':'unauthorized'})
                self.assertEqual(e.exception.code,401)
                request('/api/worker/transcript',{'id':voice['id'],'text':'Move toward the cube'},server.store.token)
                self.assertEqual(request('/api/status')['voice']['text'],'Move toward the cube')
                self.assertNotIn('audio',server.store.voice)
            finally:server.shutdown();server.server_close()

if __name__=='__main__':unittest.main()
