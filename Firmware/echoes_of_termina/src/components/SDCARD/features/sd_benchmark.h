#if !defined(SD_BENCHMARK_H)
#define SD_BENCHMARK_H

//? ------------------------------------------------------------------------------------------
//*! microSD throughput benchmark — a one-shot measurement taken right after the card is
//*! mounted, so the 1-bit bus decision on Marajoara_V3 can be backed by real numbers
//*! instead of arithmetic.
//*!
//*! Enable with -DSD_BENCHMARK=1 in platformio.ini (off by default: it writes to the card
//*! and costs a second or two at every boot).
//*!
//*! What it measures, in KB/s (bytes per millisecond):
//*!   write      512 KB in 4 KB chunks      — portal uploads / any future recording
//*!   read 4K    512 KB back in 4 KB chunks — what the decoder uses
//*!   read 512B  the same file in 512 B chunks — worst case: one FATFS sector per call
//*!   media      up to 4 MB of the largest file in /Echoes[/Music] — the real path
//*!
//*! Reference: a 320 kbps MP3 consumes 40 KB/s, uncompressed 44.1/16 stereo WAV 176 KB/s.
//? ------------------------------------------------------------------------------------------

#include "shared/dependencies.h"

#if !defined(SD_BENCHMARK)
#define SD_BENCHMARK 0
#endif

#if SD_BENCHMARK

#define SDBENCH_PATH "/Echoes/sdbench.tmp"
#define SDBENCH_CHUNK 4096
#define SDBENCH_WRITE_CHUNKS 128 // 128 × 4 KB = 512 KB
#define SDBENCH_SMALL_CHUNK 512
#define SDBENCH_MEDIA_MAX (4UL * 1024UL * 1024UL)
#define SDBENCH_MP3_KBPS 320

// One buffer, deliberately in .bss: the loop task's stack is only 8 KB.
static uint8_t sdbench_buf[SDBENCH_CHUNK];

// Sequential read of *path* in *chunk*-sized pieces (capped at *max_bytes*, 0 = whole file).
// Returns KB/s, or 0 if nothing could be read.
static uint32_t sdbench_read(const char *path, size_t chunk, size_t max_bytes, size_t *bytes_out)
{
    *bytes_out = 0;

    File f = SD_MMC.open(path, FILE_READ);
    if (!f || f.isDirectory())
    {
        if (f)
            f.close();
        return 0;
    }

    if (chunk > sizeof(sdbench_buf))
        chunk = sizeof(sdbench_buf);

    size_t total = 0;
    uint32_t t0 = micros();
    int n;
    while ((max_bytes == 0 || total < max_bytes) &&
           (n = f.read(sdbench_buf, chunk)) > 0)
    {
        total += (size_t)n;
    }
    uint32_t dt_us = micros() - t0;
    f.close();

    *bytes_out = total;
    if (total == 0 || dt_us == 0)
        return 0;
    return (uint32_t)(((uint64_t)total * 1000ULL) / dt_us); // bytes/ms == KB/s
}

// Largest file in /Echoes/Music, else the largest in /Echoes. Empty string if none.
static void sdbench_pick_media(char *out, size_t out_len, size_t *size_out)
{
    static const char *dirs[] = {"/Echoes/Music", "/Echoes"};
    size_t best = 0;
    out[0] = '\0';

    for (unsigned d = 0; d < sizeof(dirs) / sizeof(dirs[0]); d++)
    {
        File dir = SD_MMC.open(dirs[d]);
        if (!dir || !dir.isDirectory())
        {
            if (dir)
                dir.close();
            continue;
        }
        for (File e = dir.openNextFile(); e;)
        {
            // Skip the benchmark's own temp file: on a fresh card it is the
            // largest thing in /Echoes and would be measured twice.
            if (!e.isDirectory() && e.size() > best && strcmp(e.path(), SDBENCH_PATH) != 0)
            {
                best = e.size();
                snprintf(out, out_len, "%s", e.path());
            }
            e = dir.openNextFile();
        }
        dir.close();
        if (best)
            break; // prefer /Music when it has anything
    }

    if (size_out)
        *size_out = best;
}

void sd_benchmark()
{
    if (!sdcard)
    {
        Serial.println("[SDBENCH] no card mounted — skipped");
        return;
    }

    Serial.printf("[SDBENCH] card type %d, %llu MB — writing %u KB test file\n",
                  (int)SD_MMC.cardType(),
                  SD_MMC.cardSize() / (1024ULL * 1024ULL),
                  (unsigned)(SDBENCH_WRITE_CHUNKS * SDBENCH_CHUNK / 1024));

    // ── write ───────────────────────────────────────────────────────────────
    memset(sdbench_buf, 0xA5, sizeof(sdbench_buf));
    File f = SD_MMC.open(SDBENCH_PATH, FILE_WRITE);
    if (!f)
    {
        Serial.printf("[SDBENCH] cannot open %s for writing — is the card write-protected?\n",
                      SDBENCH_PATH);
        return;
    }
    uint32_t t0 = micros();
    size_t written = 0;
    for (int i = 0; i < SDBENCH_WRITE_CHUNKS; i++)
        written += f.write(sdbench_buf, sizeof(sdbench_buf));
    f.close();

    uint32_t dt_us = micros() - t0;
    uint32_t kb = (uint32_t)(written / 1024);
    Serial.printf("[SDBENCH] write      %4u KB in %5u ms = %5u KB/s%s\n",
                  kb, dt_us / 1000,
                  dt_us ? (uint32_t)(((uint64_t)written * 1000ULL) / dt_us) : 0,
                  written ? "" : "  (FAILED)");

    if (written == 0)
    {
        SD_MMC.remove(SDBENCH_PATH);
        return;
    }

    // ── reads ───────────────────────────────────────────────────────────────
    size_t n4k = 0, n512 = 0;
    uint32_t r4k = sdbench_read(SDBENCH_PATH, SDBENCH_CHUNK, 0, &n4k);
    uint32_t r512 = sdbench_read(SDBENCH_PATH, SDBENCH_SMALL_CHUNK, 0, &n512);

    Serial.printf("[SDBENCH] read 4KB   %4u KB           = %5u KB/s\n",
                  (unsigned)(n4k / 1024), r4k);
    Serial.printf("[SDBENCH] read 512B  %4u KB           = %5u KB/s\n",
                  (unsigned)(n512 / 1024), r512);

    // ── real media file, if the card has one ────────────────────────────────
    char media[128];
    size_t media_size = 0;
    sdbench_pick_media(media, sizeof(media), &media_size);
    if (media[0])
    {
        size_t nm = 0;
        uint32_t rm = sdbench_read(media, SDBENCH_CHUNK, SDBENCH_MEDIA_MAX, &nm);
        Serial.printf("[SDBENCH] media      %4u KB (%s) = %5u KB/s\n",
                      (unsigned)(nm / 1024), media, rm);
    }
    else
    {
        Serial.println("[SDBENCH] media      no file found under /Echoes");
    }

    SD_MMC.remove(SDBENCH_PATH);

    // ── verdict: headroom against the slowest measured read ─────────────────
    uint32_t slowest = r4k;
    if (r512 && (!slowest || r512 < slowest))
        slowest = r512;
    if (slowest)
        Serial.printf("[SDBENCH] %u kbps MP3 needs %u KB/s — headroom %ux (slowest read %u KB/s)\n",
                      (unsigned)SDBENCH_MP3_KBPS, (unsigned)(SDBENCH_MP3_KBPS / 8),
                      (unsigned)(slowest / (SDBENCH_MP3_KBPS / 8)), (unsigned)slowest);
    else
        Serial.println("[SDBENCH] reads failed — no throughput figure");
}

#else

// Disabled: drop -DSD_BENCHMARK=1 from platformio.ini to compile this instead.
void sd_benchmark() {}

#endif // SD_BENCHMARK

#endif // SD_BENCHMARK_H
