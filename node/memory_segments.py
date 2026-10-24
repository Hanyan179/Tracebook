"""Bounded, versioned source ranges. Context may be sent; only the focus owns proposals."""
from array import array
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from pathlib import Path
import re
import sys
import threading
import unicodedata

for root in (Path(__file__).parent / 'PythonRuntime', Path(__file__).resolve().parents[1] / 'DerivedData/PythonRuntime'):
    if (root / 'common/uniseg').is_dir():
        sys.path.insert(0, str(root / 'common'))
        break
try:
    from uniseg.graphemecluster import grapheme_cluster_boundaries
except ImportError:
    grapheme_cluster_boundaries = None

MAX_CHARACTERS = 32000
CONTEXT_CHARACTERS = 2000
FOCUS_CHARACTERS = MAX_CHARACTERS - 2 * CONTEXT_CHARACTERS
MAX_SOURCE_CHARACTERS = 2_000_000


def segment_value(value):
    if value is None:return None
    if (not isinstance(value, dict) or any(not isinstance(value.get(k), int) or isinstance(value.get(k), bool) for k in ('start','end'))
            or value['start'] < 0 or value['end'] <= value['start'] or value['end']-value['start'] > FOCUS_CHARACTERS):
        from store import StoreError
        raise StoreError(400, '片段范围不正确，请重新选择。')
    return {k:value[k] for k in ('start','end')}


def valid_version(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


class MemorySegments:
    def __init__(self, store):
        self.s = store
        self.cache = OrderedDict()
        self.cache_lock = threading.Lock()

    def error(self, status, message):
        from store import StoreError
        raise StoreError(status, message)

    def ranges(self, text, version):
        """Cache only compact ranges, never source text. Expensive work holds no database lock."""
        with self.cache_lock:
            if version in self.cache:
                self.cache.move_to_end(version)
                return self.cache[version]
        size=len(text)
        if size>MAX_SOURCE_CHARACTERS:self.error(413,'这份资料超过 200 万字符，请先拆分资料后再整理。')
        if size<=MAX_CHARACTERS:return ()
        if grapheme_cluster_boundaries is None:self.error(503,'资料分段组件未就绪，请更新或重新安装电脑应用。')
        boundaries=None;ascii_text=text.isascii()
        # UAX look-behind over pathological mark runs can become quadratic. Keep the
        # source untouched and report an unsupported range instead of tying up a worker.
        if not ascii_text:
            marks=0
            for char in text:
                marks=marks+1 if unicodedata.category(char) in ('Mn','Mc','Me','Cf') else 0
                if marks>256:self.error(413,'资料包含超长的组合字符，无法安全分段。请先手动提出记忆建议。')
        def boundary(position, forward=False):
            nonlocal boundaries
            if position<=0:return 0
            if position>=size:return size
            if ascii_text:
                return position+(1 if forward else -1) if text[position-1:position+1]=='\r\n' else position
            if boundaries is None:boundaries=array('I',grapheme_cluster_boundaries(text))
            return boundaries[bisect_left(boundaries,position) if forward else bisect_right(boundaries,position)-1]
        focus=[];start=0
        while start<size:
            proposed=min(size,start+FOCUS_CHARACTERS)
            if proposed==size:end=size
            else:
                newline=text.rfind('\n',start+FOCUS_CHARACTERS//2,proposed)
                end=newline+1 if newline>=0 else boundary(proposed)
            if end<=start:self.error(413,'资料包含超长的组合字符，无法安全分段。请先手动提出记忆建议。')
            focus.append((start,end));start=end
        result=[]
        for index,(start,end) in enumerate(focus):
            lower=max(0,start-CONTEXT_CHARACTERS);upper=min(size,end+CONTEXT_CHARACTERS)
            before=text.find('\n',lower,start)
            after=text.rfind('\n',end,upper)
            context_start=0 if lower==0 else before+1 if before>=0 else boundary(lower,True)
            context_end=size if upper==size else after+1 if after>=0 else boundary(upper)
            result.append((start,end,context_start,context_end,index+1,len(focus),size))
        result=tuple(result)
        with self.cache_lock:
            self.cache[version]=result
            while len(self.cache)>8:self.cache.popitem(last=False)
        return result

    def select(self, text, version, requested):
        requested=segment_value(requested)
        if requested is None:return None
        for start,end,before,after,index,count,total in self.ranges(text,version):
            if (start,end)==(requested['start'],requested['end']):
                return {'start':start,'end':end,'contextStart':before,'contextEnd':after,
                        'index':index,'count':count,'totalCharacters':total}
        self.error(409,'片段与当前分段方案不一致，请重新选择范围。')

    def excerpt(self, text):
        if len(text)<=160:return text
        if text.isascii():return text[:160]
        if grapheme_cluster_boundaries is None:return ''
        end=0
        for pos in grapheme_cluster_boundaries(text[:200]):
            if pos>160:break
            end=pos
        return text[:end]

    def prepare(self, kind, rid, expected=None):
        from store import canonical_id
        rid=canonical_id(rid)
        with self.s.lock:
            row,text,version=self.s.memories.source(kind,rid)
            if expected is not None and expected!=version:self.error(409,'原文已变化，请重新读取并选择片段。')
            title,date=row['title'],row['day']
        ranges=self.ranges(text,version)  # Unicode work must not block status/pause or source edits.
        units=[]
        if ranges:
            for start,end,before,after,index,count,total in ranges:
                segment={'start':start,'end':end,'contextStart':before,'contextEnd':after,'index':index,'count':count,'totalCharacters':total}
                units.append({'segment':segment,'characters':after-before,'focusCharacters':end-start,
                              'excerpt':self.excerpt(text[start:end]),'textAvailable':bool(text[start:end].strip())})
        else:
            units.append({'segment':None,'characters':len(text),'focusCharacters':len(text),
                          'excerpt':self.excerpt(text),'textAvailable':bool(text.strip())})
        with self.s.lock:
            _,_,current=self.s.memories.source(kind,rid)
            if current!=version:self.error(409,'原文在分段时发生变化，请重新读取。')
            for unit in units:
                tid,_=self.s.memory_extraction.identity(kind,rid,version,unit['segment'])
                saved=self.s.db.execute('SELECT status,result FROM memory_extractions WHERE id=?',(tid,)).fetchone()
                unit['reusable']=bool(saved and saved['result'] is not None)
                unit['status']=saved['status'] if saved else 'idle'
        return {'kind':kind,'id':rid,'title':title,'date':date,'contentVersion':version,
                'characters':len(text),'units':units,'requestLimit':20,'contextLimit':CONTEXT_CHARACTERS}

    def read(self, kind, rid, values):
        with self.s.lock:
            row,text,version=self.s.memories.source(kind,rid)
            if values.get('contentVersion')!=version:self.error(409,'原文已变化，请重新读取并选择片段。')
            title=row['title']
        selected=self.select(text,version,values.get('segment'))
        if selected is None:
            if len(text)>MAX_CHARACTERS:self.error(413,'请先选择长资料中的片段。')
            return {'title':title,'prefix':'','text':text,'suffix':'','segment':None,'contentVersion':version}
        return {'title':title,'prefix':text[selected['contextStart']:selected['start']],
                'text':text[selected['start']:selected['end']],
                'suffix':text[selected['end']:selected['contextEnd']],
                'segment':selected,'contentVersion':version}
