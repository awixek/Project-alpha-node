from __future__ import annotations
import re
from shared.constants import AgentID
from .models import ConsistencyFinding, ConsistencyReport, IssueSeverity

def audit_consistency(r,f,s,seo,v,a,voice,subs,video,thumb):
    findings=[]; checked=0; aligned=0
    vals=[getattr(x,'mission_id',None) for x in (r,f,s,seo,v,a,voice,subs,video,thumb)]; checked+=1
    if len({str(x) for x in vals})!=1: findings.append(ConsistencyFinding(source_agents=list(AgentID)[2:12],code='mission_mismatch',message='Upstream outputs do not share the same mission identifier.',severity=IssueSeverity.CRITICAL,confidence=1,evidence=[str(x) for x in vals],recommended_fix='Regenerate the mismatched artifact.'))
    else: aligned+=1
    title=str(getattr(s,'title','')).lower(); seo_title=str(getattr(seo,'optimized_title','')).lower(); checked+=1
    if title and seo_title and any(t in seo_title for t in title.split()[:3]): aligned+=1
    else: findings.append(ConsistencyFinding(source_agents=[AgentID.SCRIPT_FORGE,AgentID.SEO_BRAIN],code='script_seo_alignment',message='SEO title does not clearly preserve the script topic.',severity=IssueSeverity.WARNING,confidence=.72,evidence=[title,seo_title],recommended_fix='Align SEO title with the script topic.'))
    scenes=getattr(v,'scenes',[]) or []; aset={getattr(x,'scene_id',None) for x in getattr(a,'assets',[]) or []}; missing=[getattr(x,'scene_id',None) for x in scenes if getattr(x,'scene_id',None) not in aset]; checked+=1
    if not missing: aligned+=1
    else: findings.append(ConsistencyFinding(source_agents=[AgentID.VISION_PLANNER,AgentID.VISION_CREATOR],code='scene_asset_alignment',message=f'Vision scenes without matching assets: {missing}.',severity=IssueSeverity.ERROR,confidence=.99,evidence=[str(x) for x in missing],recommended_fix='Generate missing assets.'))
    vs=getattr(voice,'narration_segments',[]) or []; ss=[x for t in getattr(subs,'subtitle_tracks',[]) or [] for x in getattr(t,'segments',[]) or []]; checked+=1
    if vs and ss and abs(len(vs)-len(ss))<=max(2,int(len(vs)*.5)): aligned+=1
    else: findings.append(ConsistencyFinding(source_agents=[AgentID.VOICE_CORE,AgentID.SUBTITLE_ENGINE],code='voice_subtitle_alignment',message='Narration and subtitle segment coverage is materially misaligned.',severity=IssueSeverity.ERROR,confidence=.9,evidence=[str(len(vs)),str(len(ss))],recommended_fix='Re-synchronize subtitles against final narration.'))
    checked+=1; script_words=len(' '.join(str(getattr(x,'content','')) for x in getattr(s,'sections',[]) or []).split())
    if script_words>=5: aligned+=1
    else: findings.append(ConsistencyFinding(source_agents=[AgentID.SCRIPT_FORGE],code='script_content_insufficient',message='Script content is too small for meaningful semantic validation.',severity=IssueSeverity.WARNING,confidence=.9,evidence=[str(script_words)],recommended_fix='Provide the complete script.'))
    score=max(0,min(100,100-len(findings)*20+(aligned/max(1,checked))*5))
    return ConsistencyReport(score=score,aligned_pairs=aligned,checked_pairs=checked,findings=findings,explanation='Cross-agent checks compare mission identity, topic alignment, scene-to-asset coverage, and narration-to-subtitle coverage.')
