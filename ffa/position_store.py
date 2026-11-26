"""Private ledger on its own Git branch; optimistic, idempotent batch commits."""
import base64
from copy import deepcopy
import json
import re
from urllib.parse import quote
from .positions import empty_book, ordered

PATH='positions/account.json'
DEFAULT_BRANCH='ffa-positions'


class Conflict(RuntimeError):
    pass


def check_branch(branch):
    if not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_./-]*',branch) or branch in ('main','master','ffa-data') or '..' in branch:
        raise ValueError('Use a separate positions branch, not the code or market-data branch.')


def load(store, branch=DEFAULT_BRANCH, ref=None):
    check_branch(branch)
    response=store.request('GET',f'/contents/{PATH}',params={'ref':ref or branch},headers={'Accept':'application/vnd.github.object+json'})
    if response.status_code==404:return empty_book(),None
    if response.status_code!=200:raise RuntimeError('Could not read the private position ledger.')
    entry=response.json()
    if entry.get('encoding')!='base64':
        blob=store.request('GET',f'/git/blobs/{entry["sha"]}')
        if blob.status_code!=200:raise RuntimeError('Could not read the position ledger content.')
        entry['content']=blob.json()['content']
    value=json.loads(base64.b64decode(entry['content']))
    if value.get('schema')!=1:raise ValueError('Unsupported position ledger version.')
    ordered(value.get('trades',[]))
    return value,entry['sha']


def merge(base, local, remote):
    output=deepcopy(remote)
    def field(a,b,c):
        if b==a:return deepcopy(c)
        if c==a or b==c:return deepcopy(b)
        raise Conflict('The same transaction or account setting changed in another session. Reload before saving; your draft is retained.')
    a={t['id']:t for t in base['trades']};b={t['id']:t for t in local['trades']};c={t['id']:t for t in remote['trades']}
    trades=[]
    for k in sorted(set(a)|set(b)|set(c)):
        value=field(a.get(k),b.get(k),c.get(k))
        if value is not None:trades.append(value)
    output['trades']=ordered(trades)
    output['settings']=field(base.get('settings',{}),local.get('settings',{}),remote.get('settings',{}))
    for name in ('batches','migrations'):
        combined=deepcopy(remote.get(name,{}))
        for k in set(base.get(name,{}))|set(local.get(name,{})):
            value=field(base.get(name,{}).get(k),local.get(name,{}).get(k),remote.get(name,{}).get(k))
            if value is not None:combined[k]=value
        if combined:output[name]=combined
    return output


def ensure_branch(store, branch):
    check_branch(branch)
    response=store.request('GET','/git/ref/heads/'+quote(branch,safe=''))
    if response.status_code==200:return
    if response.status_code!=404:raise RuntimeError('Could not check the position storage branch.')
    repo=store.request('GET')
    if repo.status_code!=200 or repo.json().get('private') is not True:raise RuntimeError('Position storage requires a private repository.')
    head=store.request('GET','/git/ref/heads/'+quote(repo.json().get('default_branch','main'),safe=''))
    if head.status_code!=200:raise RuntimeError('Could not initialize the position storage branch.')
    result=store.request('POST','/git/refs',json={'ref':'refs/heads/'+branch,'sha':head.json()['object']['sha']})
    if result.status_code not in (201,422):raise RuntimeError('Could not create the position storage branch.')


def save(store, base, local, branch=DEFAULT_BRANCH, validate=None, batch_id=None):
    """One checked batch per commit. A lost response can be retried safely."""
    ordered(local['trades']);ensure_branch(store,branch)
    for _ in range(3):
        remote,sha=load(store,branch)
        if batch_id and batch_id in remote.get('batches',{}):return remote,sha
        value=merge(base,local,remote)
        if validate is not None:validate(remote,value)
        value['trades']=ordered(value['trades'])
        if value==remote:return remote,sha
        content=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
        if len(content)>15*1024*1024:raise ValueError('Position ledger exceeds the supported size.')
        body={'message':'Save FFA account transactions and settings','branch':branch,'content':base64.b64encode(content).decode()}
        if sha:body['sha']=sha
        response=store.request('PUT',f'/contents/{PATH}',json=body)
        if response.status_code in (409,422):continue
        if response.status_code not in (200,201):raise RuntimeError('Position save failed. The draft is retained; no success has been assumed.')
        return value,response.json()['content']['sha']
    raise Conflict('Position records changed concurrently. Reload and reconcile before saving; the draft is retained.')


def versions(store, branch=DEFAULT_BRANCH):
    check_branch(branch)
    response=store.request('GET','/commits',params={'sha':branch,'path':PATH,'per_page':30})
    if response.status_code==404:return []
    if response.status_code!=200:raise RuntimeError('Position versions could not be loaded.')
    return [{'sha':c['sha'],'date':c['commit']['committer']['date']} for c in response.json()]
