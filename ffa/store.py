import base64
import gzip
import json
import re
from contextlib import contextmanager

import requests

BRANCH = "ffa-data"
PATH = "market.json.gz"
class Repository:
    def __init__(self, name, token):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name) or not token:
            raise ValueError("GitHub repository credentials missing")
        self.url = f"https://api.github.com/repos/{name}"
        self.client = requests.Session()
        self.client.trust_env = False
        self.client.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def request(self, method, path="", **kwargs):
        return self.client.request(method, self.url + path, timeout=(8, 90), allow_redirects=False, **kwargs)

    def require_private(self):
        response = self.request("GET")
        if response.status_code != 200 or response.json().get("private") is not True:
            raise RuntimeError("GitHub storage requires a private repository")

    def read_file(self, path, ref=BRANCH):
        response = self.request("GET", f"/contents/{path}", params={"ref": ref},
                                headers={"Accept": "application/vnd.github.raw+json"})
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise RuntimeError("GitHub data read failed")
        return response.content

    def data_revision(self):
        response = self.request("GET", f"/git/ref/heads/{BRANCH}")
        if response.status_code != 200:
            raise RuntimeError("Private data branch unavailable")
        return response.json()["object"]["sha"]

    def load(self):
        response = self.request("GET", f"/contents/{PATH}", params={"ref": BRANCH},
                                headers={"Accept": "application/vnd.github.object+json"})
        if response.status_code == 404:
            branch = self.request("GET", f"/git/ref/heads/{BRANCH}")
            if branch.status_code != 200:
                raise RuntimeError("Private data branch unavailable")
            return {"quotes": {}, "observations": {}, "status": {}, "batches": [], "revisions": []}, None
        if response.status_code != 200:
            raise RuntimeError("GitHub data read failed")
        entry = response.json()
        if entry.get("encoding") != "base64":
            blob = self.request("GET", f"/git/blobs/{entry['sha']}")
            if blob.status_code != 200:
                raise RuntimeError("GitHub blob read failed")
            entry["content"] = blob.json()["content"]
        payload = json.loads(gzip.decompress(base64.b64decode(entry["content"])))
        return payload, entry["sha"]

@contextmanager
def connect(repo, token):
    store = Repository(repo, token)
    try:
        store.require_private()
        yield store
    finally:
        store.client.close()


def market(payload):
    quotes = payload["quotes"]
    rolling = []
    for source_key, observation in payload["observations"].items():
        if observation["rolling"]:
            _, series, _ = json.loads(source_key)
            rolling.append(dict(quotes[observation["key"]], rolling=observation["rolling"],
                                series=series, mapping=observation["mapping"]))
    return list(quotes.values()), rolling, list(payload["status"].values())
