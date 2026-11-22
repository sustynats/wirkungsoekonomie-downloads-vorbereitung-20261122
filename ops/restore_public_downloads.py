"""Restore only audited immutable public downloads. Never upload source archives."""
import pathlib,json,os,urllib.request,urllib.error,urllib.parse,hashlib,time
repo=os.environ['GITHUB_REPOSITORY'];token=os.environ['GH_TOKEN'];headers={'Authorization':'Bearer '+token,'User-Agent':'WOeK-Download-Restore','Accept':'application/vnd.github+json'}
def api(method,url,data=None,ctype=None):
    for attempt in range(12):
        h=dict(headers)
        if data is not None:h['Content-Type']=ctype or 'application/json'
        request=urllib.request.Request(url if url.startswith('https://') else 'https://api.github.com'+url,data=data,headers=h,method=method)
        try:
            with urllib.request.urlopen(request,timeout=180) as r:
                raw=r.read();return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail=e.read().decode('utf8','replace')
            if e.code in {403,429} and 'rate limit' in detail.lower():
                delay=max(60,int(e.headers.get('Retry-After',str(min(60*2**attempt,1800)))))
                if e.headers.get('X-RateLimit-Remaining')=='0':delay=max(delay,int(e.headers['X-RateLimit-Reset'])-int(time.time())+5)
                print(json.dumps({'phase':'provider-cooldown','seconds':delay}),flush=True);time.sleep(delay);continue
            raise RuntimeError('GITHUB_HTTP_'+str(e.code))
    raise RuntimeError('GITHUB_RATE_LIMIT_PERSISTS')
manifest=json.loads(pathlib.Path('ops/download-manifest.json').read_text());assert manifest['schema']==1 and len(manifest['assets'])==9493
releases={r['tag_name']:r for r in api('GET','/repos/'+repo+'/releases?per_page=100')}
known={}
for tag,r in releases.items():
    assets=[]
    for page in range(1,12):
        batch=api('GET','/repos/'+repo+'/releases/'+str(r['id'])+'/assets?per_page=100&page='+str(page));assets+=batch
        if len(batch)<100:break
    known[tag]={a['name']:a for a in assets}
count=0;uploaded=0;size=0;started=time.time()
for item in manifest['assets']:
    tag=item['tag'];name=item['name'];expected='sha256:'+item['sha256']
    assert tag in releases and '/' not in name and '\\' not in name
    existing=known[tag].get(name)
    if existing and existing.get('state')=='starter' and not existing.get('digest'):
        # GitHub retains unfinished upload placeholders after interrupted
        # transfers. They contain no completed publication and block its name.
        api('DELETE','/repos/'+repo+'/releases/assets/'+str(existing['id']))
        print(json.dumps({'phase':'remove-unfinished-upload-placeholder','name':name}),flush=True)
        existing=None
    if existing:
        assert existing['size']==item['bytes'] and existing.get('digest')==expected,'Existing download differs; refusing overwrite'
    else:
        assert not item['alreadySanitized'],'A sanitized technical log requires its separately reviewed copy'
        source=urllib.parse.urlparse(item['source']);assert source.scheme=='https' and source.netloc=='github.com' and source.path.startswith('/sustynats/wirkungsoekonomie.de/releases/download/')
        raw=None
        for attempt in range(5):
            try:
                with urllib.request.urlopen(urllib.request.Request(item['source'],headers={'User-Agent':'WOeK-Download-Restore'}),timeout=180) as r:raw=r.read()
                break
            except urllib.error.HTTPError as e:
                if e.code not in {429,500,502,503,504}:raise
                time.sleep(max(5,int(e.headers.get('Retry-After',str(5*2**attempt)))))
        assert raw is not None and len(raw)==item['bytes'] and hashlib.sha256(raw).hexdigest()==item['sha256'],'Original publication digest differs'
        url=releases[tag]['upload_url'].split('{')[0]+'?name='+urllib.parse.quote(name,safe='')
        try:result=api('POST',url,raw,'application/octet-stream')
        except Exception:
            # An ambiguous upload is checked against server metadata, never
            # blindly overwritten or counted as a successful delivery.
            found=[]
            for page in range(1,12):
                batch=api('GET','/repos/'+repo+'/releases/'+str(releases[tag]['id'])+'/assets?per_page=100&page='+str(page));found+=batch
                if len(batch)<100:break
            result=next((a for a in found if a['name']==name),None)
            if not result:raise
        assert result.get('digest')==expected and result['size']==item['bytes'],'Copied digest differs'
        uploaded+=1;time.sleep(2)
    count+=1;size+=item['bytes']
    if count%100==0:print(json.dumps({'checked':count,'expected':9493,'newlyCopied':uploaded,'bytes':size,'seconds':round(time.time()-started)}),flush=True)
proof={'status':'DOWNLOAD_PARITY_VERIFIED','checked':count,'expected':9493,'newlyCopied':uploaded,'bytes':size,'seconds':round(time.time()-started)}
pathlib.Path('download-parity-proof.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof),flush=True)
