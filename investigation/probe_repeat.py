import subprocess,json,time,urllib.parse,urllib.request,datetime,pathlib
root=pathlib.Path('investigation'); base=dict(q='',category='全部通知',source='all',group='wechat,official',intent='activity',page='1',view='list')
variants=[('all2',{'intent':'all','page':'2'}),('all3',{'intent':'all','page':'3'}),('list1_repeat',{})]
log=json.loads((root/'requests.json').read_text(encoding='utf-8'))
for name,changes in variants:
 if log: time.sleep(3)
 params=base|changes; url='https://pkuknow.cn/api/notices?'+urllib.parse.urlencode(params)
 start=datetime.datetime.now(datetime.timezone.utc).isoformat()
 req=urllib.request.Request(url,headers={'User-Agent':'PKURadar-Phase0/0.1 (read-only public API investigation)'})
 result=subprocess.run(['curl.exe','-sS','--max-time','40','-A','PKURadar-Phase0/0.1 (read-only public API investigation)','-o',str(root/(name+'.json')),'-w','%{http_code}',url],capture_output=True,check=True)
 status=int(result.stdout);body=(root/(name+'.json')).read_bytes()
 (root/(name+'.json')).write_bytes(body)
 data=json.loads(body); entry=dict(name=name,url=url,time=start,status=status,bytes=len(body));log.append(entry)
 print(name,status,len(body),'keys',list(data),'items',len(data.get('items',[])),flush=True)
(root/'requests.json').write_text(json.dumps(log,ensure_ascii=False,indent=2),encoding='utf-8')
