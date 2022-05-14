from pybtex.database.input import bibtex
import requests, json
import csv
import notion_api as na

with open("../notion_ids.csv", 'r') as f:
    f_csv = f.read().splitlines()
database_id = f_csv[1]
secret = f_csv[2]

headers_read = {
    "Authorization": "Bearer " + secret,
    "Notion-Version": "2022-02-22"
}
headers_query = {
    "Authorization": "Bearer " + secret,
    "Notion-Version": "2022-02-22",
    "Content-Type": "application/json"
}
headers_create = {
    "Authorization": "Bearer " + secret,
    "Content-Type": "application/json",
    "Notion-Version": "2022-02-22",
}

na.queryPage(database_id, headers_query)
#createPage(database_id, headers_create)

#open a bibtex file
parser = bibtex.Parser()
bibdata = parser.parse_file("paperpile_references.bib")


for bib_id in bibdata.entries:
    
    b = bibdata.entries[bib_id]
    
    paper_title = bibdata.entries[bib_id].fields['title']
    first_author = bib_id[:-3]
    author_list = bibdata.entries[bib_id].persons['author']
    
    if 'journal' in bibdata.entries[bib_id].fields:
        journal_name = bibdata.entries[bib_id].fields['journal']
    elif 'publisher' in bibdata.entries[bib_id].fields:
        journal_name = bibdata.entries[bib_id].fields['publisher']
    elif 'URLs' in bibdata.entries[bib_id].fields:
        journal_name = bibdata.entries[bib_id].fields['URLs']
    
    year_pub = bibdata.entries[bib_id].fields['year']
    
    if len(author_list) > 2:
        auth_year = first_author[:-4] + ' et. al.,' + year_pub
    elif len(author_list) == 1:
        auth_year = first_author[:-4] + year_pub
    if len(author_list) == 2:
        prim_auth = str(author_list[0]).split(", ")[0]
        sec_auth = str(author_list[1]).split(", ")[0]
        auth_year = prim_auth + " & " + sec_auth + ", " + year_pub
