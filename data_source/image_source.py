import hashlib
from dataclasses import dataclass
from typing import Union

from ..exception import PlatformUnsupportError
from ..utils import download_url


@dataclass
class ImageSource:
    async def get_image(self) -> bytes:
        raise NotImplementedError


@dataclass
class ImageUrl(ImageSource):
    url: str

    async def get_image(self) -> bytes:
        return await download_url(self.url)

@dataclass
class UnsupportAvatar(ImageSource):
    platform: str

    async def get_image(self) -> bytes:
        raise PlatformUnsupportError(self.platform)
