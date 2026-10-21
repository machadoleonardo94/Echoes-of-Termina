#if !defined(SERVICE_HEAP_PROBE)
#define SERVICE_HEAP_PROBE

//? ------------------------------------------------------------------------------------------
//*! Temporary boot probe — DELETE once the boot-time corruption is confirmed gone.
//*!
//*  It reports internal-RAM free bytes, the malloc-visible total and, when an
//*  external RAM heap exists, its control block read straight out of memory.
//*  The external heap registers a `multi_heap_info` at the base of PSRAM
//*  (SOC_EXTRAM_DATA_LOW = 0x3F800000); its five words are:
//*      +0  lock      +4  free_bytes     +8  minimum_free_bytes
//*      +12 pool_size +16 TLSF handle
//*  Raw reads keep working after a handle has been corrupted, whereas any heap
//*  API that dereferences it would panic. The heap_caps_* calls used here only
//*  read byte counters, so they never touch the TLSF handle either.
//? ------------------------------------------------------------------------------------------

#include "shared/dependencies.h"

void heap_probe(const char *tag)
{
    uint32_t spi_total = (uint32_t)heap_caps_get_total_size(MALLOC_CAP_SPIRAM);
    char tlsf[16];
    if (spi_total > 0)
    {
        volatile uint32_t *mi = (volatile uint32_t *)0x3F800000;
        snprintf(tlsf, sizeof(tlsf), "%08X", (unsigned)mi[4]);
    }
    else
    {
        snprintf(tlsf, sizeof(tlsf), "--none--");
    }

    Serial.printf("[PROBE] %-18s int=%uK default=%uK psram=%uK tlsf=%s\n",
                  tag,
                  (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024),
                  (unsigned)(heap_caps_get_free_size(MALLOC_CAP_DEFAULT) / 1024),
                  (unsigned)(spi_total / 1024),
                  tlsf);
}

#endif // SERVICE_HEAP_PROBE
