from . import get_default_extractors


if __name__ == "__main__":
    extractors = get_default_extractors()
    #logger.info("Found extractors", count=len(extractors), extractors=[e.name for e in extractors])
    
    for source in get_default_extractors():
        print(f"Extractor: {source.name} (weight={source.weight})")
