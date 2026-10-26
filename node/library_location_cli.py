"""Local library selection helper. Output contains identity and counts, never bodies or keys."""
import argparse
import json
import signal
import sys
from backup import BackupError, offline
from library_location import inspect_library, prepare_library


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['inspect','prepare'])
    parser.add_argument('--data',required=True)
    parser.add_argument('--snapshot')
    parser.add_argument('--output')
    parser.add_argument('--offline',action='store_true')
    args=parser.parse_args()
    def cancel(*_):raise BackupError('操作已取消，当前资料库保持不变。')
    signal.signal(signal.SIGTERM,cancel);signal.signal(signal.SIGINT,cancel)
    try:
        if args.command=='inspect':
            if args.offline:
                with offline(args.data,'资料库仍在运行，暂不能核对复制结果。请等待节点退出后重新打开。'):
                    result=inspect_library(args.data,args.output)
            else:result=inspect_library(args.data,args.output)
        else:result=prepare_library(args.data,args.snapshot,args.output)
        print(json.dumps({'ok':True,**result},ensure_ascii=False));return 0
    except BackupError as error:
        print(json.dumps({'ok':False,'error':str(error)},ensure_ascii=False));return 1
    except Exception:
        print(json.dumps({'ok':False,'error':'资料库暂时无法检查。请保留原目录，核对权限或从备份还原。'},ensure_ascii=False));return 1


if __name__=='__main__':sys.exit(main())
