#!/bin/sh
# Publish every clip in /media as a looping "live" camera, mirroring how the
# Sentinel sandbox turns ~12 h of recorded footage into simulated live streams.
#
#   /media/*.mp4|mkv|ts  ->  rtsp://$RTSP_HOST:8554/stream/<n>
#
# -re          pace at real time (one second of video per second)
# -c copy      no re-encode, so the original codec is preserved: put one HEVC
#              file in ./media to exercise the mixed H.264/H.265 path
#
# Looping: ffmpeg is restarted for each pass rather than using -stream_loop -1.
# -stream_loop keeps PTS monotonic across the loop, so a client never sees the
# discontinuity; restarting reproduces the publisher-restart behaviour the real
# grid shows (session ends, client reconnects, PTS restarts near zero).
# NOTE: the PTS-steps-backwards variant of a loop is NOT reproduced locally.

RTSP_HOST="${RTSP_HOST:-mediamtx}"

# wait for the server to accept publishers
sleep 3

n=0
for f in /media/*; do
  case "$f" in
    *.mp4|*.MP4|*.mkv|*.MKV|*.ts|*.TS|*.mov|*.MOV) ;;
    *) continue ;;
  esac
  n=$((n + 1))
  url="rtsp://${RTSP_HOST}:8554/stream/${n}"
  echo "[publisher] stream/${n} <- ${f}"
  (
    while true; do
      ffmpeg -hide_banner -loglevel warning \
             -re -i "$f" \
             -an -c copy \
             -f rtsp -rtsp_transport tcp "$url"
      echo "[publisher] stream/${n} reached the loop point, restarting in 2s"
      sleep 2
    done
  ) &
done

if [ "$n" -eq 0 ]; then
  echo "[publisher] no media files found in /media — drop some clips in ./media"
  # keep the container alive so compose doesn't thrash
  while true; do sleep 3600; done
fi

echo "[publisher] publishing ${n} camera(s)"
wait
