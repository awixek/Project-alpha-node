def prioritize(issues):
    return sorted(issues,key=lambda x:(x.severity not in {x.severity.CRITICAL,x.severity.ERROR},x.repair_priority,-x.confidence,-x.estimated_impact))
