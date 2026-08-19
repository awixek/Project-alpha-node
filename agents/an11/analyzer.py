from .models import ValidationStage, ScoreDetail, QualityIssue
from .validators import *
from .consistency import audit_consistency

def audit_all(q,c):
    checks=[(ValidationStage.RESEARCH,validate_research,(q.research,)),(ValidationStage.FACT,validate_facts,(q.facts,)),(ValidationStage.SCRIPT,validate_script,(q.script,)),(ValidationStage.SEO,validate_seo,(q.seo,c.seo_threshold)),(ValidationStage.VISUAL,validate_visual,(q.vision,q.assets)),(ValidationStage.VOICE,validate_voice,(q.voice,)),(ValidationStage.SUBTITLE,validate_subtitles,(q.subtitles,c.subtitle_threshold)),(ValidationStage.VIDEO,validate_video,(q.video,c.video_threshold)),(ValidationStage.THUMBNAIL,validate_thumbnail,(q.thumbnail,c.thumbnail_threshold))]
    details={}; issues=[]
    for stage,fn,args in checks:
        d,i=fn(*args); details[stage.value]=d; issues.extend(i)
    cr=audit_consistency(q.research,q.facts,q.script,q.seo,q.vision,q.assets,q.voice,q.subtitles,q.video,q.thumbnail)
    details['consistency']=ScoreDetail(score=cr.score,explanation=cr.explanation,factors={'aligned_pairs':cr.aligned_pairs,'checked_pairs':cr.checked_pairs})
    for f in cr.findings: issues.append(QualityIssue(stage=ValidationStage.CONSISTENCY,severity=f.severity,code=f.code,message=f.message,confidence=f.confidence,affected_agent=f.source_agents[-1] if f.source_agents else None,recommended_fix=f.recommended_fix,estimated_impact=.7 if f.severity in {f.severity.ERROR,f.severity.CRITICAL} else .3,repair_priority=2 if f.severity==f.severity.CRITICAL else 4,evidence=f.evidence))
    tracks=len(getattr(q.subtitles,'subtitle_tracks',[]) or []); acc=ScoreDetail(score=100 if tracks else 30,explanation='Accessibility score uses availability of caption tracks as the baseline hearing-access control.',factors={'subtitle_tracks':tracks}); details['accessibility']=acc
    stage_scores={k:v.score for k,v in details.items() if k not in {'accessibility'}}
    details['production']=ScoreDetail(score=sum(stage_scores.values())/max(1,len(stage_scores)),explanation='Production score summarizes all completed quality dimensions.',factors=stage_scores)
    details['educational']=ScoreDetail(score=details['script'].score,explanation='Educational quality is anchored to the script audit.',factors={'script':details['script'].score})
    details['technical']=ScoreDetail(score=(details['visual'].score+details['voice'].score+details['subtitle'].score+details['video'].score)/4,explanation='Technical quality combines visual, audio, subtitle and video integrity.',factors={'visual':details['visual'].score,'voice':details['voice'].score,'subtitle':details['subtitle'].score,'video':details['video'].score})
    details['seo']=details['seo']
    details['visual']=details['visual']
    details['audio']=details['voice']
    details['confidence']=ScoreDetail(score=max(0,min(100,100-(sum(i.confidence for i in issues)/max(1,len(issues)))*100)),explanation='Confidence score reflects the certainty of detected findings; fewer high-confidence issues produce a higher score.',factors={'issue_count':len(issues)})
    return details,issues,cr,acc
