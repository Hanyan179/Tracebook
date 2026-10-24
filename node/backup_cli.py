"""Local backup entry point. Never logs record bodies or credentials."""
import argparse
import json
from pathlib import Path
import signal
import sys
from backup import BackupError, create_backup, inspect_backup, restore_backup


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    create=commands.add_parser('create');create.add_argument('--data',required=True);create.add_argument('--destination',required=True)
    inspect=commands.add_parser('inspect');inspect.add_argument('--file',required=True);inspect.add_argument('--scratch',required=True)
    restore=commands.add_parser('restore');restore.add_argument('--file',required=True);restore.add_argument('--destination',required=True)
    restore.add_argument('--sha256',required=True);restore.add_argument('--scratch',required=True)
    args=parser.parse_args()
    def cancel(*_):raise BackupError('操作已取消，当前资料库保持不变。')
    signal.signal(signal.SIGTERM,cancel);signal.signal(signal.SIGINT,cancel)
    def progress(value):print(json.dumps(value,ensure_ascii=False),file=sys.stderr,flush=True)
    try:
        if args.command=='create':result=create_backup(args.data,args.destination,progress)
        else:
            scratch=Path(args.scratch);scratch.mkdir(parents=True,exist_ok=True,mode=0o700)
            if args.command=='inspect':result=inspect_backup(args.file,scratch,progress=progress)
            else:result=restore_backup(args.file,args.destination,args.sha256,scratch,progress)
        print(json.dumps({'ok':True,**result},ensure_ascii=False),flush=True)
        return 0
    except BackupError as error:
        print(json.dumps({'ok':False,'error':str(error)},ensure_ascii=False),flush=True);return 1
    except Exception:
        print(json.dumps({'ok':False,'error':'备份文件或保存位置不可用，当前资料库未被替换。'},ensure_ascii=False),flush=True);return 1


if __name__=='__main__':sys.exit(main())
