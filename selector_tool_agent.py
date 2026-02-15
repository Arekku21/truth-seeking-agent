from dataclasses import dataclass
import trafilatura
import string
from rank_bm25 import BM25Plus
import re
from typing import List, Dict, Any

from utils.logging_helper import get_my_logger

#!logger
logger = get_my_logger(__name__)

#this is the selector tool to choose the best links
#deleted the dataclass -> changed to just take json

#! helper function clean the text
def clean_text(text: str) -> str:
    """
    Normalizes text for matching.
    1. Handles Unicode 'invisible' characters (like \u202f in 'type\u202f2').
    2. Standardizes hyphens.
    3. Removes punctuation and collapses whitespace.
    """
    if not text: 
        return ""
    
    text = text.lower()
    
    # 2. Fix Unicode Spaces (The "LLM Artifacts")
    # \u202f = Narrow No-Break Space (Common in "Type 2", "10 kg")
    # \u00a0 = No-Break Space (&nbsp;)
    text = text.replace('\u202f', ' ').replace('\u00a0', ' ')
    
    # 3. Fix Unicode Hyphens (Common in "post-prandial")
    # \u2011 = Non-Breaking Hyphen
    # \u2013 = En Dash, \u2014 = Em Dash
    text = re.sub(r'[\u2010-\u2015\u2212\ufe58\ufe63\uff0d]', '-', text)

    # 4. Remove Punctuation (Replace with space to avoid merging "word.word")
    # We replace with space because "sugar-free" should become "sugar free", not "sugarfree"
    text = text.translate(str.maketrans(string.punctuation, ' ' * len(string.punctuation)))
    
    # 5. Collapse multiple spaces into one
    return " ".join(text.split())

#! validator function to check BM25 rank, 200 status and link contains notes
def validator_tool(search_query:str, list_of_links:List[Dict[str, Any]]) -> List[Dict[str, Any]]:

    #list of validated links
    valid_links = []
    corpus_token = []

    logger.info(f"[Validator Tool] Checking {len(list_of_links)} links for query: '{search_query[:30]}...'")

    for item in list_of_links:

        url = item["source_link"]
        url_notes = item["source_content"]

        #logging print
        logger.info(f"[Validator] Fetching URL: {url}")

        #! check if the link is fetchable
        try:

            #get the link
            download_url = trafilatura.fetch_url(url)

            if download_url is None:

                logger.warning(f"[Validator] Failed to reach webpage: {url}")
                continue
            
            #get the main body content
            url_content = trafilatura.extract(download_url)

            if not url_content:
                logger.warning(f"[Validator] Failed to extract content (Empty Body): {url}")
                continue

        except Exception as e:

            logger.error(f"[Validator] Exception processing {url}: {e}")
            continue

        #!verify the notes is in the main body content

        #cleaned texts
        cleaned_url_notes = clean_text(url_notes)
        cleaned_url_content = clean_text(url_content)

        # 2 types of matching exact and fuzzy just in case
        if cleaned_url_notes in cleaned_url_content:
            match_type = "Exact"

        else:
            #split words for fuzzy check
            url_notes_words = set(cleaned_url_notes.split())
            url_content_words = set(cleaned_url_content.split())

            if len(url_notes_words) == 0:
                logger.warning(f"[Validator] URL notes were empty/invalid for {url}. Skipping.")
                continue

            overlap = len(url_notes_words.intersection(url_content_words))
            ratio = overlap / len(url_notes_words)

            match_type = "Fuzzy"

            if ratio < 0.7:
                logger.warning(f"[Validator] Content mismatch (Ratio: {ratio:.2f}) for {url}. Agent hallucinated content?")
                continue

        #preprocess items for the BM25 ranking
        pretokenized_cleaned_content = cleaned_url_content.split()
        corpus_token.append(pretokenized_cleaned_content)

        valid_links.append(item)

        logger.debug(f"[Validator] Validated {url} (Match: {match_type})")
    
    if not valid_links:
        logger.warning("[Validator] No valid links remained after validation filters.")
        return[]
    
    #! Bm25 ranking 

    #bm25 with corpus of valid links
    bm25 = BM25Plus(corpus_token)

    #preprocess the search query
    preprocessed_search_query = clean_text(search_query).split()

    #bm25 scores
    scores = bm25.get_scores(preprocessed_search_query)

    # Assign scores back to the documents
    for i, doc in enumerate(valid_links):
        doc["bm25_score"] = scores[i]
        
    # Sort by Score (Descending)
    ranked_results = sorted(valid_links, key=lambda x: x["bm25_score"], reverse=True)

    logger.info(f"[Validator Tool] Ranking complete. Top result score: {ranked_results[0]['bm25_score']:.3f}")

    return ranked_results

