from .models import QualityConfig, ScoreDetail
def weighted_score(details,config):
    weights=config.scoring_weights; total=sum(weights.values()) or 1; return max(0,min(100,sum(d.score*weights.get(k,0) for k,d in details.items())/total))
def explain(details,score):
    weakest=', '.join(f'{k}={v.score:.1f}' for k,v in sorted(details.items(),key=lambda x:x[1].score)[:3]); return f'Overall production score is {score:.1f}/100 using configurable weighted dimensions. Weakest dimensions: {weakest}.'
