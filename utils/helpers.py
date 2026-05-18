from utils.constants import SHARE_FOLDER_PATH
import hashlib
import logging
import math
import os
import re
from pathlib import Path

from utils.constants import HASH_BUFFER_LEN, TEMP_FOLDER_PATH   
from utils.types import CompressionMethod,DirData, ItemSearchResult, Message, TransferProgress, TransferStatus


def convert_size(size_bytes: int) -> str:
    """Convert bytes to a human-readable format."""
    if size_bytes == 0:
        return "0B"
    
    size_name = ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB"]
    i = int(math.floor(math.log(size_bytes, 1024)))

    p = math.pow(1024, i)
    s = round(size_bytes/p,2)
    return f"{s}{size_name[i]}"

def get_file_hash(filepath: str) -> str:
    """ Calculate the SHA-1 hash of a file.
    Reads the file in chunks to efficiently handle large files."""
    hash = hashlib.sha1()
    with open(filepath, "rb") as f:
        while True:
            file_bytes = f.read(HASH_BUFFER_LEN)
            hash.update(file_bytes)
            if len(file_bytes) < HASH_BUFFER_LEN:
                break
    return hash.hexdigest()


def get_unique_filename(path: Path) -> str:
    """Generate a unique filename by appending a counter if the file already exists."""
    parent,filename,extension = path.parent, path.stem, path.suffix
    counter = 1
    logging.debug(f"parent:{parent}")
    logging.debug(f"making unique file for{path}")
    while path.exists():
        path = parent / Path(filename + "_" + str(counter) + extension)
        counter += 1
    logging.debug(f"unique file is {path}")
    return str(path)

def construct_message_html(message: Message, is_self: bool)-> str:
    """Construct HTML for a message, styling the sender's name based on whether it's the current user."""
    
    return f"""
        <p style="
        margin-top:0px;
        margin-bottom:0px;
        margin-left:0px;
        margin-right:0px;
        -qt-block-indent:0;      
        text-indent:0px;
        ">
        <span style=" font-weight:600; color:{'#1a5fb4' if is_self else '#e5a50a'};">
        {"You" if is_self else message["sender"]}:
        </span>
        </p>
        """

def path_to_dict(path: Path, share_folder_path: str) -> DirData:
    """
    Convert a file path to a dictionary.
    This function is used to convert file paths to a dictionary format
    that can be sent over the network.
    """
    d: DirData = {
        "path": str(path).removeprefix(share_folder_path + "/"),
        "name" : path.name,
        "hash" : None,
        "compression": CompressionMethod.NONE.value,
        "type": "",
        "size": None,
        "children": [],
    }

    if path.is_dir():
        d["type"] = "directory"
        d["children"] = [path_to_dict(item, share_folder_path) for item in path.iterdir()]

    else:
        d["type"] = "file"
        d["size"] = Path(path).stat().st_size
    
    return d

def find_file(share: list[DirData] | None, path: str, ) -> DirData | None:
    """ find a file from the given file path """

    if share is None:
        return None
    
    for item in share: 
        if item["path"] == path:
            return item
        
        else :
            s = find_file(item["children"],path)
            if s is not None:
                return s
    
    return None

def update_file_hash(share: list[DirData], file_path: str, new_hash: str) -> None:

    """update the hash value of a specified item in the dir structure """

    for item in share:

        if item["type"] == "file" and item["path"] == file_path:
            item["hash"] = new_hash
            return 
        elif item["children"]:
            update_file_hash(item["children"], file_path, new_hash)
    return

def get_files_in_dir(dir: list[DirData] | None, files: list[DirData]):
    """ Obtain only the file items in a given directory dictionary
    Store the file in files list"""

    if dir is None:
        return 
    
    for item in dir:
        if item["type"] == "file":
            files.append(item)
        else:
            get_files_in_dir(item["children"],files)

def get_directory_size(directory: DirData, size: int, count: int)-> tuple[int,int]:
    """ Calculate the directory size and contained files count for a given directory"""

    count = 0
    size = 0

    if directory["children"] is None:
        count+= 1
        size += directory["size"]
    
    else:
        for child in directory["children"]:
            if child["type"] == "file":
                count+=1
                size += child["size"]
            else:
                child_size,child_count = get_directory_size(child,0,0)
                size += child_size
                count += child_count
    
    return size,count

def item_search(dir: list[DirData] | None, items: list[ItemSearchResult], search_query:str, owner:str ):

    """Recurses a given file structure of a directory to find items that match a search string.
    On each item, the function performs a regex search for exact matches followed by a fuzzy search to capture potential spelling errors.
    Output is given in the [items] parameter. """

    
    from fuzzysearch import find_near_matches

    if dir is None:
        return
    for item in dir:
        if re.search(search_query, item["name"].lower()) is not None or find_near_matches(search_query,item["name"].lower(),max_l_dist = 1):
            items.append({
                "owner": owner,
                "data": item
            })
        
        if item["type"] == "directory":
            item_search(item["children"],items,search_query,owner)
    

def display_share_dict(share: list[DirData] | None, indents:int = 0):

    """ Prints the director structure in terminal"""

    if share is None:
        return
    
    for item in share:
        if item["type"] == "file":
            print("  "*indents+item["name"])
        else:
            print("  "*indents+ item["name"]+"/")
            display_share_dict(item["children"],indents+1)


def import_file_to_share(file_path: Path, share_folder_path: Path) -> Path | None:

    """ To generate Symlink to a given file in the user share folder path"""
    try:
        if file_path.exists():
            imported_file = share_folder_path / file_path.name #name addition is done here
            imported_file.symlink_to(file_path, target_is_directory=file_path.is_dir())
            return imported_file
        
        else:
            logging.error(f"Attempted to import file{str(file_path)} that does not exist")
            return None
    
    except Exception as e:
        logging.error(f"Error importing file{str(file_path)}: {str(e)}")
        return None


    


                


    


