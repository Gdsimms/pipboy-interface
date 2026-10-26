/* Wideband FM from the kernel RTL-SDR node, mono 48 kHz on stdout. */
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <linux/videodev2.h>
#include <sys/ioctl.h>

#define ADC_HZ 2400000
#define AUDIO_HZ 48000
#define DECIM (ADC_HZ / AUDIO_HZ)
#define BAND_HZ 200000

static volatile sig_atomic_t stop_flag;

static void on_stop(int sig) {
    (void)sig;
    stop_flag = 1;
}

static int xioctl(int fd, unsigned long request, void *arg) {
    int rc;
    do {
        rc = ioctl(fd, request, arg);
    } while (rc < 0 && errno == EINTR && !stop_flag);
    return rc;
}

static int fail(const char *what) {
    fprintf(stderr, "wbfm: %s: %s\n", what, strerror(errno));
    return 1;
}

int main(int argc, char **argv) {
    struct sigaction sa;
    struct v4l2_format fmt;
    struct v4l2_frequency freq;
    struct v4l2_control ctrl;
    double mhz;
    unsigned hz;
    unsigned bufsize;
    unsigned char *raw;
    int fd;
    int primed = 0;
    int count = 0;
    int filled = 0;
    float prev_i = 0;
    float prev_q = 0;
    float acc = 0;
    float deemph = 0;
    float prev_audio = 0;
    float hp = 0;
    float peak = 0.02f;
    const float alpha = (1.0f / AUDIO_HZ) / (75e-6f + 1.0f / AUDIO_HZ);
    const float hp_a = 0.995f;
    int16_t pcm[1024];

    if (argc != 2) {
        fprintf(stderr, "usage: wbfm MHZ\n");
        return 2;
    }
    mhz = atof(argv[1]);
    if (mhz < 87.5 || mhz > 108.0) {
        fprintf(stderr, "wbfm: frequency out of the broadcast band\n");
        return 2;
    }
    hz = (unsigned)(mhz * 1000000.0 + 0.5);

    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_stop;
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    signal(SIGPIPE, SIG_IGN);

    fd = open("/dev/swradio0", O_RDWR);
    if (fd < 0)
        return fail("open /dev/swradio0");

    memset(&fmt, 0, sizeof fmt);
    fmt.type = V4L2_BUF_TYPE_SDR_CAPTURE;
    fmt.fmt.sdr.pixelformat = V4L2_SDR_FMT_CU8;
    if (xioctl(fd, VIDIOC_S_FMT, &fmt) < 0)
        return fail("set format");
    bufsize = fmt.fmt.sdr.buffersize;
    if (bufsize < 4096 || bufsize > 1048576)
        bufsize = 65536;

    memset(&freq, 0, sizeof freq);
    freq.tuner = 0;
    freq.type = V4L2_TUNER_ADC;
    freq.frequency = ADC_HZ;
    if (xioctl(fd, VIDIOC_S_FREQUENCY, &freq) < 0)
        return fail("set sample rate");

    memset(&ctrl, 0, sizeof ctrl);
    ctrl.id = V4L2_CID_RF_TUNER_BANDWIDTH_AUTO;
    ctrl.value = 0;
    if (xioctl(fd, VIDIOC_S_CTRL, &ctrl) < 0)
        return fail("bandwidth auto");
    ctrl.id = V4L2_CID_RF_TUNER_BANDWIDTH;
    ctrl.value = BAND_HZ;
    if (xioctl(fd, VIDIOC_S_CTRL, &ctrl) < 0)
        return fail("bandwidth");

    memset(&freq, 0, sizeof freq);
    freq.tuner = 1;
    freq.type = V4L2_TUNER_RF;
    freq.frequency = hz;
    if (xioctl(fd, VIDIOC_S_FREQUENCY, &freq) < 0)
        return fail("tune");

    raw = malloc(bufsize);
    if (!raw)
        return fail("buffer");

    while (!stop_flag) {
        unsigned char *p;
        ssize_t n = read(fd, raw, bufsize);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            fail("read");
            break;
        }
        if (n < 2)
            break;
        for (p = raw; p + 1 < raw + n; p += 2) {
            float i = (float)p[0] - 127.5f;
            float q = (float)p[1] - 127.5f;
            float real, imag, audio, mag;
            int sample;
            if (!primed) {
                prev_i = i;
                prev_q = q;
                primed = 1;
                continue;
            }
            real = i * prev_i + q * prev_q;
            imag = prev_i * q - prev_q * i;
            prev_i = i;
            prev_q = q;
            acc += atan2f(imag, real);
            if (++count < DECIM)
                continue;
            audio = acc / (float)DECIM;
            acc = 0;
            count = 0;
            deemph += alpha * (audio - deemph);
            hp = hp_a * (hp + deemph - prev_audio);
            prev_audio = deemph;
            mag = fabsf(hp);
            peak += (mag > peak ? 0.04f : 0.0006f) * (mag - peak);
            if (peak < 0.01f)
                peak = 0.01f;
            sample = (int)(hp / peak * 22000.0f);
            if (sample > 32767)
                sample = 32767;
            if (sample < -32767)
                sample = -32767;
            pcm[filled++] = (int16_t)sample;
            if (filled == (int)(sizeof pcm / sizeof pcm[0])) {
                if (write(STDOUT_FILENO, pcm, sizeof pcm) < 0) {
                    stop_flag = 1;
                    break;
                }
                filled = 0;
            }
        }
    }
    if (filled > 0)
        write(STDOUT_FILENO, pcm, (size_t)filled * sizeof pcm[0]);
    free(raw);
    close(fd);
    return 0;
}
