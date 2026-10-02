"""Trusted native probes in a bounded, networkless Script Docker container."""
from pathlib import Path
import json
import subprocess
import uuid

REPO=Path(__file__).resolve().parents[3]

def run(locale, output):
    profile=json.loads((REPO/'config/runtimes.json').read_text())['9.0']
    source=REPO/'tests/corpus/welcome-calendar/native-probes'
    name='element-test-native35-'+uuid.uuid4().hex[:12]
    command=['docker','run','--rm','--name',name,'--pull','never','--network','none','--read-only','--cap-drop','ALL',
             '--security-opt','no-new-privileges','--user','65534:65534','--memory','384m','--cpus','1','--pids-limit','64',
             '--tmpfs','/tmp:rw,noexec,nosuid,size=64m','--workdir','/tmp',
             '--mount',f'type=bind,source={REPO/profile["directory"]},target=/runtime,readonly',
             '--mount',f'type=bind,source={source},target=/generated,readonly',profile['image'],
             'java','-Xmx128m','-XX:MaxMetaspaceSize=128m','-XX:+UseSerialGC',
             '--add-opens','java.base/java.lang=ALL-UNNAMED','--add-opens','java.base/java.nio=ALL-UNNAMED',
             '-Dfile.encoding=UTF-8','-Djava.io.tmpdir=/tmp','-Dlogs.root=/tmp',
             '-Dlogback.configurationFile=/runtime/config/logback.xml','-Dexecutor.location=/runtime',
             '-Duser.language='+locale[:2],'-Duser.country='+locale[3:],'-cp','/runtime/lib/*',
             'com.e1c.g5rt.executor.boot.ExecutorBootstrap','-c','9.0','/generated/Calendar.sbsl']
    try:
        p=subprocess.run(command,capture_output=True,text=True,timeout=30)
        output.mkdir(parents=True,exist_ok=True)
        (output/(locale+'.stdout')).write_text(p.stdout)
        (output/(locale+'.stderr')).write_text(p.stderr)
        if p.returncode:raise RuntimeError(p.stderr or p.stdout)
        return json.loads(p.stdout)
    finally:
        subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=10)

if __name__=='__main__':
    out=REPO/'result/dvizhok-welcome-calendar/native-probes'
    for locale in ['ru-RU','en-US']:
        actual=run(locale,out)
        (out/(locale+'.json')).write_text(json.dumps(actual,ensure_ascii=False,indent=2)+'\n')
        print(locale,actual['day'],actual['time'],actual['missingGet'])
