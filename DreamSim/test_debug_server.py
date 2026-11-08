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
                # Legacy recording/auth endpoints still serve historical main-worker jobs.
                # New public commands route through the unified fleet (covered separately).
                command={'id':'a'*32,'prompt':'Move toward cube','status':'queued','stop':False}
                server.store.command=command
                server.store.event({'kind':'command.queued','request_id':command['id'],'prompt':command['prompt']})
                server.store.fleet['worker_at']=time.time()
                server.store.fleet['tables']=[{'index':0,'ready':True}]
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
                request('/api/worker/frame',dict(frame,recording=False,request_id=None,sequence=2),server.store.token)
                self.assertEqual(request('/api/live')['frame']['sequence'],2)
                self.assertEqual(request('/api/replay/0')['frame']['sequence'],1)
                self.assertEqual(request('/api/live')['count'],1)
                video={'request_id':command['id'],'prompt':'Move toward cube','frames':2,'result':'failed','video':base64.b64encode(b'0000ftyp'+b'x'*100).decode()}
                with self.assertRaises(urllib.error.HTTPError) as e:request('/api/worker/video',video)
                self.assertEqual(e.exception.code,401)
                request('/api/worker/video',video,server.store.token)
                self.assertEqual(request('/api/status')['recording']['frames'],2)
                request('/api/worker/video',dict(video,role='wrist_l'),server.store.token)
                request('/api/worker/video',dict(video,role='wrist_r'),server.store.token)
                self.assertEqual(set(request('/api/status')['recording']['roles']),{'overhead','wrist_l','wrist_r'})
                with urllib.request.urlopen(url+'/api/recording/wrist_l.mp4') as response:self.assertEqual(len(response.read()),108)
                self.assertEqual(Store(Path(directory)).recording['request_id'],command['id'])
                with urllib.request.urlopen(url+'/api/recording.mp4') as response:
                    self.assertEqual(response.headers['Content-Type'],'video/mp4')
                    self.assertEqual(len(response.read()),108)

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

    def test_recording_identity_ranges_and_legacy_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);first='a'*32;second='b'*32
            old=b'0000ftyp'+bytes(range(100));new=b'0000ftyp'+b'N'*120
            (path/'recording.json').write_text(json.dumps({'request_id':first,'prompt':'old','frames':4,'result':'failed','roles':['overhead']}))
            (path/'recording-overhead.mp4').write_bytes(old)
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.store=Store(path)
            threading.Thread(target=server.serve_forever,daemon=True).start();url='http://127.0.0.1:'+str(server.server_port)
            def post(data):
                req=urllib.request.Request(url+'/api/worker/video',data=json.dumps(data).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+server.store.token})
                return urllib.request.urlopen(req)
            def get(path,byte_range=None):
                return urllib.request.urlopen(urllib.request.Request(url+path,headers={'Range':byte_range} if byte_range else {}))
            try:
                server.store.event({'kind':'command.queued','request_id':second,'frames':{'video.overhead':'large-unused-history'}})
                payload={'request_id':second,'prompt':'new','frames':5,'result':'failed','video':base64.b64encode(new).decode()}
                with post(payload) as response:self.assertEqual(response.status,200)
                with get('/api/recording/overhead.mp4?id='+first) as response:self.assertEqual(response.read(),old)
                with get('/api/recording/overhead.mp4?id='+second,'bytes=4-15') as response:
                    self.assertEqual(response.status,206);self.assertEqual(response.headers['Content-Range'],'bytes 4-15/128');self.assertEqual(response.read(),new[4:16])
                with get('/api/recording/overhead.mp4?id='+second,'bytes=-7') as response:self.assertEqual(response.read(),new[-7:])
                with get('/api/recording/overhead.mp4?id='+second,'bytes=120-') as response:self.assertEqual(response.read(),new[120:])
                for byte_range in ('bytes=999-','bytes=6-2','bytes=-0','bytes=0-2,4-6'):
                    with self.assertRaises(urllib.error.HTTPError) as error:get('/api/recording/overhead.mp4?id='+second,byte_range)
                    self.assertEqual(error.exception.code,416)
                with self.assertRaises(urllib.error.HTTPError) as error:post(dict(payload,video=base64.b64encode(old).decode()))
                self.assertEqual(error.exception.code,409)
                with self.assertRaises(urllib.error.HTTPError) as error:get('/api/recording/overhead.mp4?id='+'c'*32)
                self.assertEqual(error.exception.code,404)
                with get('/api/status') as response:self.assertNotIn('frames',json.load(response)['events'][-1])
            finally:server.shutdown();server.server_close()

if __name__=='__main__':unittest.main()
