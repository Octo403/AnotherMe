import json
from pathlib import Path

from aitown.memory import MemoryStore
from .models import ProfileClaim, StyleProfile
from .statistics import statistics


class ProfileStore:
    def __init__(self, repo):
        self.repo=repo

    def generate(self):
        claims=[]
        for group in statistics(self.repo)['groups']:
            n=group['replies']
            scope=group['scope']
            claims.append(ProfileClaim(key=scope+':length',scope=scope,basis='observed',source_ids=group['source_ids'],
                text=f"在该会话的 {n} 组训练回复中，中位长度 {group['median_chars']} 字，{group['short_replies_le12']} 组不超过12字。按任务需要简短回应，复杂说明仍应写完整。"))
            claims.append(ProfileClaim(key=scope+':lines',scope=scope,basis='observed',source_ids=group['source_ids'],
                text=f"该会话 {group['multiline_replies']}/{n} 组回复分行表达，{group['emoji_replies']}/{n} 组含表情；适量分行，不强加表情。"))
            frequent=sorted(((t,c) for t,c in group['tokens'].items() if c),key=lambda pair:(-pair[1],pair[0]))[:4]
            if frequent:
                claims.append(ProfileClaim(key=scope+':words',scope=scope,basis='observed',source_ids=group['source_ids'],
                    text='该会话训练回复出现的口语及次数：'+ '、'.join(f'{t}({c})' for t,c in frequent)+'。按场景使用，避免机械重复。'))
            for m in MemoryStore(self.repo).query(category='preference',scope=scope):
                claims.append(ProfileClaim(key=scope+':preference:'+m.entry_key,scope=scope,basis='direct',
                    source_ids=[e.message_id for e in m.evidence],text='有来源的自述偏好：'+m.value+'；具体事实回答仍须引用事实证据。'))
        return StyleProfile(claims=claims)

    def apply(self, profile):
        profile=StyleProfile.model_validate(profile)
        changed=False
        desired={'style-profile:'+c.scope+':'+c.key for c in profile.claims}
        with self.repo.conn:
            for old in self.repo.conn.execute("SELECT id,scene,status FROM persona_traits WHERE scene LIKE 'style-profile:%'").fetchall():
                if old['scene'] not in desired and old['status']!='disabled':
                    self.repo.conn.execute("UPDATE persona_traits SET status='disabled' WHERE id=?",(old['id'],))
                    changed=True
            for claim in profile.claims:
                ids=self.repo._source_ids(claim.source_ids)
                for mid in ids:
                    source=self.repo.conn.execute("SELECT * FROM message_refs WHERE id=?",(mid,)).fetchone()
                    if source['speaker_id']!='self' or source['direction']!='outgoing' or source['conversation_id']!=claim.scope:
                        raise ValueError('Profile evidence must be scoped self speech')
                    if not self.repo.conn.execute("SELECT 1 FROM episode_messages em JOIN style_examples s ON s.episode_id=em.episode_id WHERE em.message_id=? AND s.split='train'",(mid,)).fetchone():
                        raise ValueError('Profile evidence must belong to training episodes')
                if claim.basis=='inferred' and claim.status=='confirmed':raise ValueError('Inference must remain candidate')
                status='disabled' if not claim.enabled else 'candidate' if claim.status=='candidate' else 'active' if claim.basis=='direct' else 'observed'
                basis='direct' if claim.basis=='direct' else 'inferred'
                scene='style-profile:'+claim.scope+':'+claim.key
                row=self.repo.conn.execute("SELECT * FROM persona_traits WHERE scene=?",(scene,)).fetchone()
                if row:
                    tid=row['id']
                    old_ids={s.message_id for s in self.repo.sources('trait',tid)}
                    if row['trait']==claim.text and row['basis']==basis and row['status']==status and old_ids==set(ids):
                        claim.trait_id=tid
                        continue
                    self.repo.conn.execute("UPDATE persona_traits SET trait=?,basis=?,status=? WHERE id=?",(claim.text,basis,status,tid))
                    self.repo.conn.execute('DELETE FROM trait_sources WHERE trait_id=?',(tid,))
                else:
                    tid=self.repo.conn.execute('INSERT INTO persona_traits(trait,scene,basis,status) VALUES(?,?,?,?)',(claim.text,scene,basis,status)).lastrowid
                self.repo.conn.executemany('INSERT INTO trait_sources VALUES(?,?)',[(tid,mid) for mid in ids])
                claim.trait_id=tid
                changed=True
            if changed:self.repo._bump('trait')
        return profile

    def active(self, profile, scope):
        result=[]
        for claim in profile.claims:
            if claim.scope!=scope or not claim.enabled or claim.status!='confirmed' or claim.basis=='inferred':continue
            row=self.repo.conn.execute("SELECT * FROM persona_traits WHERE id=?",(claim.trait_id,)).fetchone()
            ids=[s.message_id for s in self.repo.sources('trait',claim.trait_id)] if row else []
            if row and row['status'] in ('active','observed') and row['trait']==claim.text and set(ids)==set(claim.source_ids):result.append(claim)
        return result

    @staticmethod
    def read(path):
        return StyleProfile.model_validate_json(Path(path).read_text(encoding='utf-8'))

    @staticmethod
    def write(profile,path):
        path=Path(path)
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(profile.model_dump(),ensure_ascii=False,indent=2),encoding='utf-8')
