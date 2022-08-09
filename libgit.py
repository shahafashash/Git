from argparse import ArgumentParser
from collections import OrderedDict
from configparser import ConfigParser
from pathlib import Path
from typing import List
import hashlib
import pathvalidate
from pathvalidate.argparse import sanitize_filepath_arg
import re
import sys
import os
import zlib


class GitObject:
    def __init__(self, path: str, obj_type: str, data: bytes = None) -> None:
        self.path: str = str(
            Path(pathvalidate.sanitize_filepath(path, platform="auto")).resolve()
        )
        self.type: str = obj_type
        self.size: int = None
        self.hash: str = None
        self.data: bytes = None

        # deserialize data if provided
        if data is not None:
            self.deserialize(data)

    def serialize(self) -> bytes:
        """
        Serialize the object to bytes.
        """
        raise NotImplementedError

    def deserialize(self, data: bytes) -> None:
        """
        Deserialize the object from bytes.
        """
        raise NotImplementedError

    def __str__(self) -> str:
        """
        Return a string representation of the object.
        """
        raise NotImplementedError


class GitBlob(GitObject):
    def __init__(self, path: str, data: bytes = None) -> None:
        super().__init__(path, "blob", data)
        self.size = len(self.data)

        header = f"{self.type} {self.size}\x00".encode("utf-8")
        header_with_data = header + self.data
        self.hash = hashlib.sha1(header_with_data).hexdigest()

    def serialize(self) -> bytes:
        return self.data

    def deserialize(self, data: bytes) -> None:
        self.data = data

    def __str__(self) -> str:
        return f"{self.type} {self.size} {self.hash}\n{self.data.decode()}"


class GitRepository:
    """Represents a Git repository."""

    def __init__(self) -> None:
        self.worktree: str = None
        self.git_dir: str = None
        self.config: ConfigParser = None

    def init(self, repo: str, force: bool = False) -> None:
        """Creates a new directory for the repo and initializes a '.git' directory.

        Args:
            repo (str): Path to the new repo.
            force (bool): If True, overwrites the existing repo.
        """
        self.worktree = repo
        self.git_dir = self._get_git_dir()
        self.config = ConfigParser()

        # create the new repository
        try:
            self.worktree = self._create_new_repository(repo)
        except Exception as e:
            print(f"[!] An error occurred while creating the repository: {e}")
            raise e

        if not (force or Path(self.worktree).joinpath(".git").exists()):
            print(f"[!] Repository already exists: {self.worktree}")
            raise FileExistsError(f"Repository already exists: {self.worktree}")

        config_file = self._get_config_file()
        if Path(config_file).exists():
            self.config.read(config_file)
        elif not force:
            print(f"[!] Config file not found: {config_file}")
            raise FileNotFoundError(f"Config file not found: {config_file}")

        if not force:
            version = self.config.get("core", "repositoryformatversion")
            if version != "0":
                print(f"Unsupported repository format: {version}")
                raise ValueError(f"Unsupported repository format: {version}")

        print(f"Initialized empty Git repository in {self.worktree}")

    def cat_file(
        self,
        object_hash: str,
        ptype: bool = False,
        psize: bool = False,
        pprint: bool = True,
    ) -> None:
        """Reads the object from the object store and pretty-prints it's content and metadata.

        Args:
            object_hash (str): The hash of the object to print.
            ptype (bool): Whether to print the object type.
            psize (bool): Whether to print the object size.
            pprint (bool): Pretty-print the object based on the object type.
        """
        # find the object
        path = self._find_object(object_hash)
        if path is None:
            return
        # read the object
        obj = self._read_object(path)
        if pprint:
            print(obj)
        elif ptype:
            print(obj.type)
        elif psize:
            print(obj.size)
        else:
            print(obj.serialize())

    def hash_object(self, path: str, obj_type: str, write: bool = False) -> None:
        """Hashes the object at the given path and returns the hash.

        Args:
            path (str): Path to the object to hash.
            obj_type (str): The type of the object.
            write (bool): Whether to write the object to the object store.
        """
        # read the object file
        sanitized_path = pathvalidate.sanitize_filepath(path, platform="auto")
        resolved_path = Path(sanitized_path).resolve()
        content = resolved_path.read_bytes()
        # create the object based on the type
        if obj_type == "blob":
            obj = GitBlob(resolved_path, content)
        elif obj_type == "commit":
            obj = GitCommit(resolved_path, content)
        elif obj_type == "tag":
            obj = GitTag(resolved_path, content)
        elif obj_type == "tree":
            obj = GitTree(resolved_path, content)
        else:
            raise ValueError(f"Invalid object type: {obj_type}")

        obj_hash = self._write_object(obj, write)
        print(obj_hash)

    def _read_object(self, hashed_object: str) -> GitObject:
        """Reads an object from the repository.

        Args:
            hashed_object (str): The hash of the object.

        Raises:
            ValueError: If the object's size is invalid.
            ValueError: If the object's type is invalid.

        Returns:
            GitObject: The object read from the repository.
        """
        # get the path to the object file
        path = self._get_object_path(hashed_object)
        # read the compressed object
        compressed_data = Path(path).read_bytes()
        # decompress the object
        data = self._decompress_object(compressed_data)
        # read the object type
        type_index = data.find(b" ")
        object_type = data[:type_index].decode("utf-8")
        # read the object size
        size_index = data.find(b"\x00", type_index)
        object_size = int(data[type_index + 1 : size_index].decode("ascii"))
        # validate the object size
        if len(data) != object_size + size_index + 1:
            raise ValueError(f"Invalid object size: {object_size}")
        # create the object based on the type
        data_index = size_index + 1
        if object_type == "blob":
            git_object = GitBlob(self, data[data_index:])
        elif object_type == "tree":
            git_object = GitTree(self, data[data_index:])
        elif object_type == "commit":
            git_object = GitCommit(self, data[data_index:])
        elif object_type == "tag":
            git_object = GitTag(self, data[data_index:])
        else:
            raise ValueError(f"Invalid object type: {object_type}")

        return git_object

    def _find_object(
        self, name: str, obj_type: str = None, follow: bool = True
    ) -> None:
        # for now, it we only return the name. Will implement later.
        return name

    def _write_object(self, obj: GitObject, actually_write: bool = True) -> str:
        # serialize the object
        data = obj.serialize()
        # create a header for the object
        header = f"{obj.type} {len(data)}\x00".encode("utf-8")
        obj_with_header = header + data
        # create the object hash
        object_hash = obj.hash

        if actually_write:
            # get the path to the object file
            path = self._get_object_path(object_hash)
            # compress the object
            compressed_data = self._compress_object(obj_with_header)
            # write the compressed object
            Path(path).write_bytes(compressed_data)

        return object_hash

    def _find_index(self) -> str:
        """Finds the index file in the .git directory.

        Returns:
            str: Path to the index file.
        """
        # find the .git directory
        git_path = Path(self._get_git_dir())
        path = git_path.joinpath("index")
        return pathvalidate.sanitize_filepath(str(path.resolve()), platform="auto")

    def _compress_object(self, data: bytes, level: int = 9) -> bytes:
        """Compress object data.

        Args:
            data (bytes): The object to compress.
            level (int): The compression level (1-9).

        Returns:
            bytes: The compressed object.
        """
        # compress the object
        return zlib.compress(data, level)

    def _decompress_object(self, data: bytes) -> bytes:
        """Decompress object data.

        Args:
            data (bytes): The object to decompress.

        Returns:
            bytes: The decompressed object.
        """
        # decompress the object
        return zlib.decompress(data)

    def _get_object_path(self, hashed_object: str) -> str:
        """Returns the path to the object.

        Args:
            hashed_object (str): The hash of the object.

        Returns:
            str: The path to the object.
        """
        # get the first two characters of the hash
        first_two_chars = hashed_object[:2]
        # get the rest of the hash
        rest_of_hash = hashed_object[2:]
        # get the path to the object
        path = self._get_object_dir(first_two_chars).joinpath(rest_of_hash)
        return pathvalidate.sanitize_filepath(str(path.resolve()), platform="auto")

    def _get_object_dir(self, first_two_chars: str) -> str:
        """Returns the path to the object directory.

        Args:
            first_two_chars (str): The first two characters of the hash.

        Returns:
            str: The path to the object directory.
        """
        # get the path to the object directory
        git_dir = Path(self._get_git_dir())
        path = git_dir.joinpath("objects").joinpath(first_two_chars)
        return pathvalidate.sanitize_filepath(str(path.resolve()), platform="auto")

    def _get_config_file(self) -> str:
        """Returns the path to the config file.

        Returns:
            str: The path to the config file.
        """
        # get the path to the config file
        git_dir = Path(self._get_git_dir())
        path = git_dir.joinpath("config")
        return pathvalidate.sanitize_filepath(str(path.resolve()), platform="auto")

    def _get_git_dir(self) -> str:
        """Returns the path to the .git directory.

        Returns:
            str: The path to the .git directory.
        """
        # get the path to the .git directory
        cwd = Path(self.worktree).resolve()
        git_dir = cwd.joinpath(".git")
        if not git_dir.exists():
            raise FileNotFoundError(f"Directory is not a Git repository: {str(cwd)}")
        return pathvalidate.sanitize_filepath(str(git_dir.resolve()))

    def _create_default_config(self) -> None:
        """Creates the default config file."""
        # create the config file
        config_file = self._get_config_file()
        config = ConfigParser()

        # add the default sections
        config.add_section("core")
        config.set("core", "repositoryformatversion", "0")
        config.set("core", "filemode", "false")
        config.set("core", "bare", "false")

        # write the config file
        with open(config_file, "w") as f:
            config.write(f)

    def _create_new_repository(self, repo: str) -> str | None:
        """Creates a new repository.

        Args:
            repo (str): Path to the new repo.
        """
        # sanitize the path
        sanitized_repo = pathvalidate.sanitize_filepath(repo, platform="auto")
        # resolve the path
        resolved_repo = Path(sanitized_repo).resolve()
        # check if a valid path
        if not pathvalidate.is_valid_filepath(resolved_repo, platform="auto"):
            raise ValueError("Invalid path: {}".format(resolved_repo))
        # check if the path already exists and if so, check if it is a git repo. If so, raise an error.
        elif resolved_repo.exists():
            if resolved_repo.joinpath(".git").exists():
                raise ValueError(
                    "Path already exists and is a git repo: {}".format(resolved_repo)
                )
            # check if there are permissions to write to the path
            elif not os.access(str(resolved_repo), os.W_OK):
                raise ValueError("Path is not writable: {}".format(resolved_repo))
        # create the repo directory
        resolved_repo.mkdir(parents=True)
        # create the .git directory
        git_dir = resolved_repo.joinpath(".git")
        git_dir.mkdir()
        # create all the necessary directories
        for dname in ["objects", "refs", "refs/heads", "refs/tags"]:
            git_dir.joinpath(dname).mkdir()
        # create the HEAD file
        head_file = git_dir.joinpath("HEAD")
        head_file.write_text("ref: refs/heads/master\n")

        # create the description file
        description_file = git_dir.joinpath("description")
        description_file.write_text(
            "Unnamed repository; edit this file to name the repository.\n"
        )

        # create the default config file
        self._create_default_config()

        # return the path to the repo
        return str(resolved_repo)


def main(argv: List[str] = sys.argv[1:]) -> None:
    git = GitRepository()
    # create an argument parser
    parser = ArgumentParser(description="Own implementation of git")
    # create a subparser for the commands
    commands = parser.add_subparsers(dest="command", title="Commands", required=True)

    # create a subparser for the init command
    init = commands.add_parser("init", help="Initialize a new empty repository.")
    # add the repo argument
    init.add_argument("repo", nargs="?", default=".", help="Path to the new repo.")
    # bind the action to the function
    init.set_defaults(func=git.init)

    # create a subparser for the cat-file command
    cat_file = commands.add_parser(
        "cat-file", help="Show information about a repository object."
    )
    # add the type argument
    cat_file.add_argument(
        "-t",
        "--type",
        choices=["blob", "commit", "tag", "tree"],
        help="The type of the object.",
    )
    # add the size argument
    cat_file.add_argument(
        "-s", "--size", action="store_true", help="Show the size of the object."
    )
    # add the pretty argument
    cat_file.add_argument(
        "-p", "--pretty", action="store_true", help="Pretty print the object."
    )
    # add the hash argument
    cat_file.add_argument("hash", help="The hash of the object.")
    # bind the action to the function
    cat_file.set_defaults(func=git.cat_file)

    # create a subparser for the hash-object command
    hash_object = commands.add_parser(
        "hash-object", help="Compute the hash of a file or a blob object."
    )
    # add the type argument
    hash_object.add_argument(
        "-t",
        "--type",
        choices=["blob", "commit", "tag", "tree"],
        default="blob",
        help="The type of the object.",
    )
    # add the write argument
    hash_object.add_argument(
        "-w",
        "--write",
        action="store_true",
        help="Write the object into the object database.",
    )
    # add the path argument
    hash_object.add_argument(
        "path",
        type=sanitize_filepath_arg,
        help="The path to the file to read the object from.",
    )
    # bind the action to the function
    hash_object.set_defaults(func=git.hash_object)
