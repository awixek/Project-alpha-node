from __future__ import annotations
import re
from typing import Any
from shared.constants import AgentID
from .models import IssueSeverity, QualityIssue, ScoreDetail, ValidationStage

def issue(stage,code,message,confidence,agent,fix,impact,severity=IssueSeverity.WARNING,evidence=()):
    return QualityIssue(stage=stage,code=code,message=message,confidence=confidence,affected_agent=agent,recommended_fix=fix,estimated_impact=min(1,max(0,impact)),repair_priority=max(1,min(10,int(round((1-impact)*10)))),severity=severity,evidence=list(evidence))
def validate_research(x):
    cs=getattr(x,'candidates',[]) or []; refs=sum(len(getattr(c,'sources',[]) or []) for c in cs); div=len({getattr(s,'publisher',None) or getattr(s,'url','') for c in cs for s in getattr(c,'sources',[]) or []}); incomplete=sum(getattr(c,'information_completeness',0)<.5 for c in cs); issues=[]
    if not cs: issues.append(issue(ValidationStage.RESEARCH,'research_empty','No research candidates were supplied.',1,AgentID.RESEARCH_CORE,'Re-run AN-01.',1,IssueSeverity.CRITICAL))
    score=100-(35 if not refs else 0)-(15 if refs and div<2 else 0)-min(30,incomplete*10)
    return ScoreDetail(score=max(0,score),explanation='Research quality reflects candidate availability, source coverage, diversity and completeness.',factors={'candidates':len(cs),'source_refs':refs,'source_diversity':div,'incomplete':incomplete}),issues
def validate_facts(x):
    cs=getattr(x,'claims',[]) or []; bad=sum(str(getattr(c,'verification_status','')).lower() in {'contradicted','unsupported','outdated'} for c in cs); manual=sum(getattr(c,'manual_review_required',False) for c in cs); issues=[]
    if not cs: issues.append(issue(ValidationStage.FACT,'facts_missing','No verified claims are available.',1,AgentID.FACT_GUARDIAN,'Run AN-02 verification.',1,IssueSeverity.CRITICAL))
    if bad: issues.append(issue(ValidationStage.FACT,'fact_reliability_risk',f'{bad} claims have verification risks.',.95,AgentID.FACT_GUARDIAN,'Resolve contradicted or unsupported claims.',bad/max(1,len(cs)),IssueSeverity.ERROR))
    score=100-(bad/max(1,len(cs)))*55-(manual/max(1,len(cs)))*15
    return ScoreDetail(score=max(0,score),explanation='Fact quality reflects verification status and manual-review requirements.',factors={'claims':len(cs),'risky':bad,'manual_review':manual}),issues
def validate_script(x):
    sections=getattr(x,'sections',[]) or []; text=' '.join(str(getattr(s,'content','')) for s in sections); repeats=len(re.findall(r'\b(\w+)\b(?:(?:\W+\1\b){2,})',text.lower())); issues=[]
    if not sections or not text.strip(): issues.append(issue(ValidationStage.SCRIPT,'script_empty','Script contains no usable sections.',1,AgentID.SCRIPT_FORGE,'Regenerate AN-03 output.',1,IssueSeverity.CRITICAL))
    score=100-min(35,repeats*8)-(10 if len(sections)<2 else 0)
    return ScoreDetail(score=max(0,score),explanation='Script quality considers section completeness and repeated phrasing.',factors={'sections':len(sections),'repetition_patterns':repeats}),issues
def validate_seo(x,threshold):
    score=float(getattr(x,'seo_score',0) or 0); issues=[]
    if not str(getattr(x,'optimized_title','')).strip(): issues.append(issue(ValidationStage.SEO,'seo_title_missing','Optimized SEO title is missing.',1,AgentID.SEO_BRAIN,'Generate a valid title.',1,IssueSeverity.CRITICAL))
    if score<threshold: issues.append(issue(ValidationStage.SEO,'seo_below_threshold',f'SEO score {score:.1f} is below {threshold:.1f}.',.95,AgentID.SEO_BRAIN,'Improve SEO package.',(threshold-score)/100,IssueSeverity.ERROR))
    return ScoreDetail(score=max(0,min(100,score)),explanation='AN-04 SEO score checked against the configured threshold.',factors={'seo_score':score}),issues
def validate_visual(v,a):
    scenes=getattr(v,'scenes',[]) or []; aset=getattr(a,'assets',[]) or []; missing=[getattr(s,'scene_id',None) for s in scenes if not any(getattr(x,'scene_id',None)==getattr(s,'scene_id',None) for x in aset)]; issues=[]
    if not scenes: issues.append(issue(ValidationStage.VISUAL,'vision_empty','VisionPlan contains no scenes.',1,AgentID.VISION_PLANNER,'Regenerate AN-05.',1,IssueSeverity.CRITICAL))
    if missing: issues.append(issue(ValidationStage.VISUAL,'visual_assets_missing',f'Missing assets for scenes {missing}.',.99,AgentID.VISION_CREATOR,'Generate missing assets.',1,IssueSeverity.ERROR))
    return ScoreDetail(score=max(0,100-len(missing)/max(1,len(scenes))*70),explanation='Visual quality compares planned scenes with generated assets.',factors={'scenes':len(scenes),'assets':len(aset),'missing':len(missing)}),issues
def validate_voice(v):
    seg=getattr(v,'narration_segments',[]) or []; bad=sum(getattr(s,'duration',0)<=0 for s in seg); issues=[]
    if not seg: issues.append(issue(ValidationStage.VOICE,'voice_missing','No narration segments were generated.',1,AgentID.VOICE_CORE,'Generate AN-07 narration.',1,IssueSeverity.CRITICAL))
    if bad: issues.append(issue(ValidationStage.VOICE,'voice_invalid_timing',f'{bad} narration segments have invalid duration.',1,AgentID.VOICE_CORE,'Repair narration timing.',bad/max(1,len(seg)),IssueSeverity.ERROR))
    return ScoreDetail(score=max(0,100-bad/max(1,len(seg))*70),explanation='Audio quality checks narration presence and timing validity.',factors={'segments':len(seg),'invalid':bad}),issues
def validate_subtitles(s,threshold):
    tracks=getattr(s,'subtitle_tracks',[]) or []; seg=[z for t in tracks for z in getattr(t,'segments',[]) or []]; overlaps=sum(getattr(b,'start_time',0)<getattr(a,'end_time',0) for a,b in zip(seg,seg[1:])); score=100-overlaps/max(1,len(seg))*80; issues=[]
    if overlaps: issues.append(issue(ValidationStage.SUBTITLE,'subtitle_overlap',f'{overlaps} subtitle overlaps detected.',.99,AgentID.SUBTITLE_ENGINE,'Rebuild subtitle timing.',overlaps/max(1,len(seg)),IssueSeverity.ERROR))
    if score<threshold: issues.append(issue(ValidationStage.SUBTITLE,'subtitle_below_threshold',f'Subtitle score {score:.1f} is below {threshold:.1f}.',.95,AgentID.SUBTITLE_ENGINE,'Improve synchronization.',(threshold-score)/100,IssueSeverity.ERROR))
    return ScoreDetail(score=max(0,score),explanation='Subtitle quality checks tracks and chronological overlap.',factors={'tracks':len(tracks),'segments':len(seg),'overlaps':overlaps}),issues
def validate_video(v,threshold):
    uri=str(getattr(v,'video_uri','') or '').strip(); timeline=getattr(v,'timeline',None); scenes=getattr(timeline,'scenes',[]) if timeline else []; score=100 if uri else 20; score-=30 if not scenes else 0; issues=[]
    if not uri: issues.append(issue(ValidationStage.VIDEO,'video_output_missing','Final video URI is missing.',1,AgentID.VIDEO_FORGE,'Complete render/export.',1,IssueSeverity.CRITICAL))
    if score<threshold: issues.append(issue(ValidationStage.VIDEO,'video_below_threshold',f'Video score {score:.1f} is below {threshold:.1f}.',.95,AgentID.VIDEO_FORGE,'Repair timeline/render output.',(threshold-score)/100,IssueSeverity.ERROR))
    return ScoreDetail(score=max(0,score),explanation='Video quality checks render output and timeline integrity.',factors={'timeline_scenes':len(scenes),'render_uri':bool(uri)}),issues
def validate_thumbnail(t,threshold):
    concepts=getattr(t,'ranked_concepts',[]) or []; top=float(getattr(getattr(t,'ctr_report',None),'top_score',0) or 0); issues=[]
    if not concepts: issues.append(issue(ValidationStage.THUMBNAIL,'thumbnail_missing','No thumbnail concepts are available.',1,AgentID.THUMBNAIL_STUDIO,'Generate AN-10 concepts.',1,IssueSeverity.CRITICAL))
    if top<threshold: issues.append(issue(ValidationStage.THUMBNAIL,'thumbnail_below_threshold',f'Top CTR score {top:.1f} is below {threshold:.1f}.',.9,AgentID.THUMBNAIL_STUDIO,'Improve top thumbnail concept.',(threshold-top)/100,IssueSeverity.ERROR))
    return ScoreDetail(score=top,explanation='Thumbnail score uses the AN-10 ranked top CTR score.',factors={'concepts':len(concepts),'top_ctr':top}),issues
