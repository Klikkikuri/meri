"""
Paywalled content detection using structured data in HTML.

Supports JSON-LD, Microdata, and RDFa formats.

AI-generated code notice:
This file contains code generated or assisted by AI (GitHub Copilot).

See:
 - <https://developers.google.com/search/docs/appearance/structured-data/paywalled-content>
 - <https://schema.org/isAccessibleForFree>

"""

import json
from typing import Any, Dict, List, Union

from lxml import html
from structlog import get_logger

logger = get_logger(__name__)


def is_paywalled_content(html_string: str) -> bool:
    """
    Detect if HTML content is marked as paywalled using structured data.
    
    Supports:
    - JSON-LD structured data
    - Microdata (itemProp)
    - RDFa (property attributes)

    Technically, page might contain paywalled and non-paywalled content, so this function may return True even if only
    part of the content is paywalled.

    :param html_string: The HTML content as a string.
    :return: True if paywalled indicators are found, False otherwise.
    """
    try:
        tree = html.fromstring(html_string)
    except Exception:
        logger.exception("Failed to parse HTML", exc_info=True)
        return False
    
    # Check JSON-LD structured data
    if _check_jsonld_paywall(tree):
        return True
    
    # Check microdata
    if _check_microdata_paywall(tree):
        return True
    
    # Check RDFa
    if _check_rdfa_paywall(tree):
        return True
    
    return False

def _check_jsonld_paywall(tree) -> bool:
    """Check JSON-LD structured data for paywall indicators."""
    # Find all JSON-LD script tags
    scripts = tree.xpath('//script[@type="application/ld+json"]')
    
    for script in scripts:
        if script.text is None:
            continue
            
        try:
            data = json.loads(script.text)
            if _is_jsonld_paywalled(data):
                return True
        except (json.JSONDecodeError, TypeError):
            # Invalid JSON, skip
            logger.debug("Invalid JSON in JSON-LD script, skipping")
            continue
    
    return False

def _is_jsonld_paywalled(data: Union[Dict, List]) -> bool:
    """
    Recursively check JSON-LD data for paywall indicators.
    
    AI warning: This function is written by AI and may need review.
    """
    if isinstance(data, list):
        return any(_is_jsonld_paywalled(item) for item in data)
    
    if not isinstance(data, dict):
        return False
    
    # Check if this is a relevant schema type
    schema_type = data.get('@type', '')
    if isinstance(schema_type, list):
        schema_type = ' '.join(schema_type)
    
    relevant_types = ['NewsArticle', 'Article', 'BlogPosting', 'WebPage', 'CreativeWork']
    if any(t in schema_type for t in relevant_types):
        # Check isAccessibleForFree property
        is_free = data.get('isAccessibleForFree')
        if is_free is False or is_free == 'false' or is_free == 'False':
            return True
    
    # Check hasPart for paywalled sections
    has_part = data.get('hasPart', [])
    if isinstance(has_part, list):
        for part in has_part:
            if isinstance(part, dict):
                is_free = part.get('isAccessibleForFree')
                if is_free is False or is_free == 'false' or is_free == 'False':
                    return True
    elif isinstance(has_part, dict):
        is_free = has_part.get('isAccessibleForFree')
        if is_free is False or is_free == 'false' or is_free == 'False':
            return True
    
    # Recursively check nested objects
    for value in data.values():
        if isinstance(value, (dict, list)) and _is_jsonld_paywalled(value):
            return True
    
    return False

def _check_microdata_paywall(tree) -> bool:
    """Check microdata for paywall indicators using XPath."""
    # Look for elements with itemprop="isAccessibleForFree"
    xpath_queries = [
        '//*[@itemprop="isAccessibleForFree"]',
        '//*[contains(@itemprop, "isAccessibleForFree")]',
        '//*[@itemProp="isAccessibleForFree"]',  # Case variations
        '//*[contains(@itemProp, "isAccessibleForFree")]'
    ]
    
    for xpath in xpath_queries:
        try:
            elements = tree.xpath(xpath)
            for element in elements:
                # Check content attribute
                content = element.get('content', '').lower()
                if content in ['false', 'no', '0']:
                    return True
                
                # Check text content
                text = (element.text or '').strip().lower()
                if text in ['false', 'no', '0']:
                    return True
        except Exception:
            continue
    
    # Check for schema.org itemtype with isAccessibleForFree
    article_xpath = '//*[contains(@itemtype, "schema.org/Article") or contains(@itemtype, "schema.org/NewsArticle")]'
    try:
        article_elements = tree.xpath(article_xpath)
        for article in article_elements:
            # Look for isAccessibleForFree within this article scope
            free_elements = article.xpath('.//*[@itemprop="isAccessibleForFree" or @itemProp="isAccessibleForFree"]')
            for elem in free_elements:
                content = elem.get('content', '').lower()
                text = (elem.text or '').strip().lower()
                if content in ['false', 'no', '0'] or text in ['false', 'no', '0']:
                    return True
    except Exception:
        pass
    
    return False

def _check_rdfa_paywall(tree) -> bool:
    """Check RDFa properties for paywall indicators using XPath."""
    # Look for elements with property="isAccessibleForFree"
    xpath_queries = [
        '//*[@property="isAccessibleForFree"]',
        '//*[contains(@property, "isAccessibleForFree")]'
    ]
    
    for xpath in xpath_queries:
        try:
            elements = tree.xpath(xpath)
            for element in elements:
                # Check content attribute
                content = element.get('content', '').lower()
                if content in ['false', 'no', '0']:
                    return True
                
                # Check text content
                text = (element.text or '').strip().lower()
                if text in ['false', 'no', '0']:
                    return True
        except Exception:
            continue
    
    return False

def analyze_paywall_details(html_string: str) -> Dict[str, Any]:
    """
    Provide detailed analysis of paywall indicators in HTML.
    
    Returns:
        dict: Detailed information about paywall detection
    """
    try:
        tree = html.fromstring(html_string)
    except Exception:
        return {'is_paywalled': False, 'error': 'Failed to parse HTML'}
    
    result = {
        'is_paywalled': False,
        'detection_methods': [],
        'schema_types_found': [],
        'paywall_elements': []
    }
    
    # Analyze JSON-LD
    jsonld_results = _analyze_jsonld_details(tree)
    if jsonld_results['found_paywall']:
        result['is_paywalled'] = True
        result['detection_methods'].append('JSON-LD')
        result['schema_types_found'].extend(jsonld_results['schema_types'])
        result['paywall_elements'].extend(jsonld_results['elements'])
    
    # Analyze microdata
    microdata_results = _analyze_microdata_details(tree)
    if microdata_results['found_paywall']:
        result['is_paywalled'] = True
        result['detection_methods'].append('Microdata')
        result['paywall_elements'].extend(microdata_results['elements'])
    
    # Analyze RDFa
    rdfa_results = _analyze_rdfa_details(tree)
    if rdfa_results['found_paywall']:
        result['is_paywalled'] = True
        result['detection_methods'].append('RDFa')
        result['paywall_elements'].extend(rdfa_results['elements'])
    
    return result

def _analyze_jsonld_details(tree) -> Dict[str, Any]:
    """Analyze JSON-LD for detailed paywall information."""
    result = {'found_paywall': False, 'schema_types': [], 'elements': []}
    
    scripts = tree.xpath('//script[@type="application/ld+json"]')
    
    for i, script in enumerate(scripts):
        if script.text is None:
            continue
            
        try:
            data = json.loads(script.text)
            details = _extract_jsonld_paywall_details(data, f'script[{i}]')
            if details['found_paywall']:
                result['found_paywall'] = True
                result['schema_types'].extend(details['schema_types'])
                result['elements'].extend(details['elements'])
        except (json.JSONDecodeError, TypeError) as e:
            result['elements'].append({
                'type': 'JSON-LD Error',
                'error': str(e),
                'script_index': i
            })
    
    return result

def _extract_jsonld_paywall_details(data: Union[Dict, List], path: str = '') -> Dict[str, Any]:
    """Extract detailed paywall information from JSON-LD data."""
    result = {'found_paywall': False, 'schema_types': [], 'elements': []}
    
    if isinstance(data, list):
        for i, item in enumerate(data):
            sub_result = _extract_jsonld_paywall_details(item, f"{path}[{i}]")
            if sub_result['found_paywall']:
                result['found_paywall'] = True
                result['schema_types'].extend(sub_result['schema_types'])
                result['elements'].extend(sub_result['elements'])
        return result
    
    if not isinstance(data, dict):
        return result
    
    schema_type = data.get('@type', '')
    if isinstance(schema_type, list):
        schema_type = ' '.join(schema_type)
    
    relevant_types = ['NewsArticle', 'Article', 'BlogPosting', 'WebPage', 'CreativeWork']
    if any(t in schema_type for t in relevant_types):
        result['schema_types'].append(schema_type)
        
        is_free = data.get('isAccessibleForFree')
        if is_free is False or is_free == 'false' or is_free == 'False':
            result['found_paywall'] = True
            result['elements'].append({
                'type': 'JSON-LD',
                'schema_type': schema_type,
                'property': 'isAccessibleForFree',
                'value': is_free,
                'path': path
            })
    
    # Check nested objects
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            sub_result = _extract_jsonld_paywall_details(value, f"{path}.{key}")
            if sub_result['found_paywall']:
                result['found_paywall'] = True
                result['schema_types'].extend(sub_result['schema_types'])
                result['elements'].extend(sub_result['elements'])
    
    return result

def _analyze_microdata_details(tree) -> Dict[str, Any]:
    """Analyze microdata for detailed paywall information."""
    result = {'found_paywall': False, 'elements': []}
    
    xpath_queries = [
        '//*[@itemprop="isAccessibleForFree" or @itemProp="isAccessibleForFree"]',
        '//*[contains(@itemprop, "isAccessibleForFree") or contains(@itemProp, "isAccessibleForFree")]'
    ]
    
    for xpath in xpath_queries:
        try:
            elements = tree.xpath(xpath)
            for element in elements:
                content = element.get('content', '').lower()
                text = (element.text or '').strip().lower()
                
                if content in ['false', 'no', '0'] or text in ['false', 'no', '0']:
                    result['found_paywall'] = True
                    result['elements'].append({
                        'type': 'Microdata',
                        'tag': element.tag,
                        'itemprop': element.get('itemprop') or element.get('itemProp'),
                        'content': element.get('content'),
                        'text': (element.text or '').strip()[:100],  # Limit text length
                        'xpath': tree.getpath(element)
                    })
        except Exception:
            continue
    
    return result

def _analyze_rdfa_details(tree) -> Dict[str, Any]:
    """Analyze RDFa for detailed paywall information."""
    result = {'found_paywall': False, 'elements': []}
    
    xpath_queries = [
        '//*[@property="isAccessibleForFree"]',
        '//*[contains(@property, "isAccessibleForFree")]'
    ]
    
    for xpath in xpath_queries:
        try:
            elements = tree.xpath(xpath)
            for element in elements:
                content = element.get('content', '').lower()
                text = (element.text or '').strip().lower()
                
                if content in ['false', 'no', '0'] or text in ['false', 'no', '0']:
                    result['found_paywall'] = True
                    result['elements'].append({
                        'type': 'RDFa',
                        'tag': element.tag,
                        'property': element.get('property'),
                        'content': element.get('content'),
                        'text': (element.text or '').strip()[:100],  # Limit text length
                        'xpath': tree.getpath(element)
                    })
        except Exception:
            continue
    
    return result

# Example usage and testing
if __name__ == "__main__":
    import sys
    from ._extractors import trafilatura_extractor

    if len(sys.argv) != 2:
        print("Usage: python -m meri.extractor._paywalled <url>")
        sys.exit(1)

    url = sys.argv[1]

    html_content = trafilatura_extractor(url).html

    # Basic paywall detection
    is_paywalled = is_paywalled_content(html_content)
    print(f"Is paywalled: {is_paywalled}")
    
    # Detailed analysis
    details = analyze_paywall_details(html_content)
    print(f"\nDetailed analysis:")
    print(f"Paywalled: {details['is_paywalled']}")
    print(f"Detection methods: {details['detection_methods']}")
    print(f"Schema types found: {details['schema_types_found']}")
    print(f"Paywall elements: {len(details['paywall_elements'])}")
    
    for element in details['paywall_elements']:
        print(f"  - {element}")
