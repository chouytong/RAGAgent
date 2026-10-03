from ragagent.domain.research import MetadataFilter


def constrain_filters(proposed: MetadataFilter, requested: MetadataFilter) -> MetadataFilter:
    # Explicit user constraints cannot be relaxed by a planner/model.
    data = proposed.model_dump()
    for name, value in requested.model_dump().items():
        if value is not None and value != []:
            data[name] = value
    return MetadataFilter.model_validate(data)
