from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from PIL import Image, ImageChops, ImageStat

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


@dataclass(frozen=True, slots=True)
class ImageMatch:
    """Describe a byte-identical image and an optional visual review hint.

    Args:
        exact_path: Existing image with identical SHA-256, if found.
        similar_path: Static image with a nearby dHash, for human review only.
        distance: Number of differing dHash bits for the visual hint.
    """

    exact_path: Path | None = None
    similar_path: Path | None = None
    distance: int | None = None
    equivalent_path: Path | None = None
    equivalent_kind: str = ""


class AutoCollectImageIndex:
    """Cache file fingerprints per pack, with bounded least-recently-used packs."""

    def __init__(self, max_packs: int = 8):
        """Initialize an index safe for concurrent background lookups.

        Args:
            max_packs: Maximum number of pack manifests retained in memory.
        """
        self.max_packs = max(1, max_packs)
        self._packs: OrderedDict[Path, dict[Path, tuple[int, int, str, int | None]]] = (
            OrderedDict()
        )
        self._lock = RLock()
        self._decoded = OrderedDict()
        self._duplicate_refresh = {}
        self._duplicate_directories = {}
        self._pack_directories = {}
        self._duplicate_results = OrderedDict()
        self._duplicate_misses = OrderedDict()

    def _decoded_digest(self, path):
        """Lossless decoded equality includes every animation frame and timing."""
        try:
            stat=path.stat();key=(path,stat.st_size,stat.st_mtime_ns)
            if key in self._decoded:
                self._decoded.move_to_end(key)
                return self._decoded[key]
            with Image.open(path) as image:
                frames=int(getattr(image,'n_frames',1))
                if frames>100 or image.width*image.height*frames>8_000_000:return None
                digest=hashlib.sha256()
                digest.update(repr((image.size,frames,image.info.get('loop') if frames>1 else None)).encode())
                for index in range(frames):
                    image.seek(index)
                    if frames>1:digest.update(str(image.info.get('duration',0)).encode()+b':')
                    digest.update(image.convert('RGBA').tobytes())
            result=digest.hexdigest()
            if (path.stat().st_size,path.stat().st_mtime_ns)!=(stat.st_size,stat.st_mtime_ns):return None
            self._decoded[key]=result
            while len(self._decoded)>512:self._decoded.popitem(last=False)
            return result
        except (OSError,ValueError,Image.DecompressionBombError):return None

    @staticmethod
    def _compression_equivalent(first,second):
        """Very close full-image pixels, including local caption regions.

        dHash alone is never sufficient: matching character art with changed
        text must survive as a new meme. Uncertain matches stay advisory.
        """
        try:
            with Image.open(first) as a,Image.open(second) as b:
                if getattr(a,'n_frames',1)>1 or getattr(b,'n_frames',1)>1:return False
                if max(a.width*a.height,b.width*b.height)>4_000_000:return False
                if min(*a.size,*b.size)<64:return False
                if abs(a.width/a.height-b.width/b.height)>0.002:return False
                aa=a.convert('RGBA');bb=b.convert('RGBA')
                if aa.getextrema()[3]!=(255,255) or bb.getextrema()[3]!=(255,255):return False
                width=min(384,a.width,b.width);size=(width,round(width*a.height/a.width))
                if size[1]>2048:return False
                aa=aa.convert('RGB').resize(size,Image.Resampling.LANCZOS)
                bb=bb.convert('RGB').resize(size,Image.Resampling.LANCZOS)
            difference=ImageChops.difference(aa,bb)
            if max(ImageStat.Stat(difference).mean)>3.5:return False
            for y in range(0,size[1],16):
                for x in range(0,size[0],16):
                    box=(x,y,min(x+16,size[0]),min(y+16,size[1]))
                    if max(ImageStat.Stat(difference.crop(box)).mean)>10:return False
            return True
        except (OSError,ValueError,Image.DecompressionBombError):return False

    def lookup_duplicate(self,pack_root,digest,candidate_path,*,exclude_category=None,refresh_seconds=0):
        """Confirmed byte/pixel/compression duplicates; no visual-only guesses."""
        root=Path(pack_root).resolve()
        def usable(path):return path is not None and (not exclude_category or exclude_category not in path.relative_to(root).parts)
        with self._lock:
            now=time.monotonic()
            fresh=(root in self._packs and now-self._duplicate_refresh.get(root,0)<refresh_seconds)
            if fresh:
                try:fresh=all(folder.stat().st_mtime_ns==stamp for folder,stamp in self._duplicate_directories.get(root,[]))
                except OSError:fresh=False
            visual_missing=(candidate_path is not None and any(v[3]==-1 for v in self._packs.get(root,{}).values()))
            if not fresh or visual_missing:
                self.lookup(root,digest,candidate_path)
                self._duplicate_refresh[root]=now
                self._duplicate_directories[root]=self._pack_directories.get(root,[])
            entries=list(self._packs.get(root,{}).items())
            for path,signature in entries:
                if signature[2]==digest and usable(path) and path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root):
                    stat=path.stat()
                    if (stat.st_size,stat.st_mtime_ns)==signature[:2]:return ImageMatch(exact_path=path)
            if candidate_path is None:return ImageMatch()
            cache_key=(root,digest,exclude_category)
            cached=self._duplicate_results.get(cache_key)
            if cached:
                result,signature=cached;path=result.equivalent_path
                if path and path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root):
                    stat=path.stat()
                    if (stat.st_size,stat.st_mtime_ns)==signature:
                        self._duplicate_results.move_to_end(cache_key)
                        return result
                self._duplicate_results.pop(cache_key,None)
            if self._duplicate_misses.get(cache_key)==self._duplicate_refresh.get(root):
                self._duplicate_misses.move_to_end(cache_key)
                return ImageMatch()
            if not entries:
                self._duplicate_misses[cache_key]=self._duplicate_refresh[root]
                while len(self._duplicate_misses)>2048:self._duplicate_misses.popitem(last=False)
                return ImageMatch()
            candidate_hash=self._difference_hash(candidate_path)
            decoded=None
            # Detailed comparisons are bounded. Uncertain or crowded matches
            # remain new candidates rather than causing an expensive full scan.
            detailed=0
            for path,(size,mtime,existing_digest,visual_hash) in entries:
                if not usable(path):continue
                if candidate_hash is None:
                    # Animated files never match a static first frame. Only a
                    # lossless equality of all frames/timings may merge them.
                    if visual_hash is not None:continue
                elif visual_hash is None or (candidate_hash^visual_hash).bit_count()>2:
                    continue
                if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):continue
                stat=path.stat()
                if (stat.st_size,stat.st_mtime_ns)!=(size,mtime):
                    self._duplicate_refresh[root]=0
                    continue
                if existing_digest==digest:return ImageMatch(exact_path=path)
                if detailed>=8:break
                detailed+=1
                if decoded is None:decoded=self._decoded_digest(Path(candidate_path))
                if candidate_hash is None and decoded is None:break
                other=self._decoded_digest(path)
                result=None
                if decoded and other==decoded:
                    result=ImageMatch(equivalent_path=path,equivalent_kind='decoded_pixels')
                elif candidate_hash is not None and self._compression_equivalent(candidate_path,path):
                    result=ImageMatch(equivalent_path=path,equivalent_kind='compression')
                if result:
                    stat=path.stat();self._duplicate_results[cache_key]=(result,(stat.st_size,stat.st_mtime_ns))
                    while len(self._duplicate_results)>1024:self._duplicate_results.popitem(last=False)
                    return result
            self._duplicate_misses[cache_key]=self._duplicate_refresh[root]
            while len(self._duplicate_misses)>2048:self._duplicate_misses.popitem(last=False)
        return ImageMatch()

    def invalidate(self,pack_root):
        with self._lock:self._duplicate_refresh.pop(Path(pack_root).resolve(),None)

    @staticmethod
    def _difference_hash(path: Path) -> int | None:
        """Decode a bounded static image for an advisory visual fingerprint.

        Args:
            path: Image to inspect.

        Returns:
            A 64-bit dHash, or None for animated, oversized, or invalid images.
        """
        try:
            with Image.open(path) as image:
                if (
                    getattr(image, "is_animated", False)
                    or image.width * image.height > 40_000_000
                ):
                    return None
                with image.convert("L") as grayscale:
                    with grayscale.resize((9, 8), Image.Resampling.LANCZOS) as small:
                        pixels = small.tobytes()
                fingerprint = 0
                for y in range(8):
                    for x in range(8):
                        fingerprint = (fingerprint << 1) | (
                            pixels[y * 9 + x] > pixels[y * 9 + x + 1]
                        )
                return fingerprint
        except (OSError, ValueError, Image.DecompressionBombError):
            return None

    def lookup(
        self, pack_root: Path, digest: str, candidate_path: Path | None = None
    ) -> ImageMatch:
        """Refresh cheap file stats and find an exact match or a review hint.

        This method performs disk reads and image decoding. Async callers must
        use a worker thread. Only changed files are hashed again. Visual hashes
        are computed lazily when a candidate image is supplied. Approximate
        matches must never be treated as duplicates: text variants may matter.

        Args:
            pack_root: Directory containing the target pack's images.
            digest: SHA-256 of the candidate's original bytes.
            candidate_path: Optional candidate file for visual similarity hints.

        Returns:
            Exact match or closest static-image hint within five differing bits.
        """
        root = Path(pack_root).resolve()
        with self._lock:
            previous = self._packs.pop(root, {})
            current = {}
            # 遍历前排除符号链接，包括 Windows 目录联接。
            directories = [root]
            directory_signatures = []
            visited = set()
            while directories:
                directory = directories.pop()
                try:
                    resolved = directory.resolve()
                    if not resolved.is_relative_to(root) or resolved in visited:
                        continue
                    visited.add(resolved)
                    stamp=directory.stat().st_mtime_ns
                    children = list(directory.iterdir())
                    directory_signatures.append((directory,stamp))
                except (OSError, RuntimeError):
                    continue
                for path in children:
                    try:
                        if path.is_symlink() or not path.resolve().is_relative_to(root):
                            continue
                        if path.is_dir():
                            directories.append(path)
                            continue
                        if (
                            path.suffix.lower() not in IMAGE_EXTENSIONS
                            or not path.is_file()
                        ):
                            continue
                        stat = path.stat()
                        signature = (stat.st_size, stat.st_mtime_ns)
                        cached = previous.get(path)
                        if cached is not None and cached[:2] == signature:
                            if candidate_path is not None and cached[3] == -1:
                                cached = (*cached[:3], self._difference_hash(path))
                            current[path] = cached
                            continue
                        hasher = hashlib.sha256()
                        with path.open("rb") as source:
                            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                                hasher.update(chunk)
                        # -1 表示尚未请求；None 表示无法进行视觉匹配，
                        # 例如图片是动图。
                        visual_hash = (
                            self._difference_hash(path)
                            if candidate_path is not None
                            else -1
                        )
                        # 文件若在并发操作中被替换，下次必须重新读取。
                        after = path.stat()
                        if signature != (after.st_size, after.st_mtime_ns):
                            continue
                        current[path] = (*signature, hasher.hexdigest(), visual_hash)
                    except (OSError, RuntimeError):
                        continue
            self._packs[root] = current
            self._pack_directories[root]=directory_signatures
            while len(self._packs) > self.max_packs:
                self._packs.popitem(last=False)
            for path, (_, _, existing_digest, _) in current.items():
                if digest == existing_digest:
                    return ImageMatch(exact_path=path)
            if candidate_path is None:
                return ImageMatch()
            candidate_hash = self._difference_hash(candidate_path)
            if candidate_hash is None:
                return ImageMatch()
            closest = ImageMatch()
            for path, (_, _, _, visual_hash) in current.items():
                if visual_hash is None:
                    continue
                distance = (candidate_hash ^ visual_hash).bit_count()
                if distance <= 5 and (
                    closest.distance is None or distance < closest.distance
                ):
                    closest = ImageMatch(similar_path=path, distance=distance)
            return closest
