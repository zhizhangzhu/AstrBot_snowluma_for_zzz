#!/usr/bin/env python3
"""Build the additional Qzone actions from one pinned, already patched bundle."""
from pathlib import Path
import argparse
import hashlib

ROOT=Path(__file__).resolve().parents[1]
BASE_SHA='28a2970ac5d393cb70201d08c5afe196f13121e2a5361f30ef04818d2fe39883'  # set from reviewed input


def prepare(source, destination):
    data=source.read_bytes()
    if hashlib.sha256(data).hexdigest()!=BASE_SHA:
        raise ValueError('SnowLuma bundle changed; review the patch before applying')
    text=data.decode()
    marker='var QzoneApi = class {'
    action='\tdefineAction({\n\t\tname: "get_qzone_msg_detail",'
    if text.count(marker)!=1 or text.count(action)!=1:
        raise ValueError('Unexpected SnowLuma action layout')
    text=text.replace(marker,(ROOT/'overrides/qzone-interactions/native-functions.js').read_text()+'\n'+marker,1)
    text=text.replace(action,(ROOT/'overrides/qzone-interactions/native-actions.js').read_text()+action,1)
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(text)
    print(hashlib.sha256(text.encode()).hexdigest())


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);parser.add_argument('destination',type=Path)
    args=parser.parse_args();prepare(args.source,args.destination)
