## Notion Paperpile Integration
The current integration provides a way to create a Notion Table for entries in Paperpile to provide an efficient reference manager. 
Current implementation is adapted for personal use but can be easily modified for any field of research.

First, create a Notion Workspace with columns as desired. Here is a template for reference. The names and number of columns can be
modified as per the need.
<img width="1250" alt="Screen Shot 2022-05-15 at 2 10 05 AM" src="https://user-images.githubusercontent.com/68425163/168459651-d8ade4c9-8091-4323-bd45-263eda57da2c.png">
Create a Notion Integration as described here: https://developers.notion.com/docs/getting-started. Add the Integration to your Notion 
Workspace by sharing it. Save the database_id and secret in a "notion_ids.csv" file a directory outside the local repository.

Next step is to create a GitHub repository and use Paperpile BibTeX support to auto-export bibliography. This is very efficient and creates changes
to bibliography on GitHub in real-time.

Edit the newPage.json to account for the changes in column-names, if any. Use the script to auto-update entries on Notion. Happy Reading!!!
