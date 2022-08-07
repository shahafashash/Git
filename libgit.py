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
                print(f"[!] Unsupported repository format: {version}")
                raise ValueError(f"Unsupported repository format: {version}")

        print(f"[+] Initialized empty Git repository in {self.worktree}")

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
