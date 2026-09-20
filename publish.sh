#!/bin/sh
# Publish every clip in /media as a looping "live" camera, mirroring how the
# Sentinel sandbox turns ~12 h of recorded footage into simulated live streams.
#
#   /media/*.mp4|mkv|ts  ->  rtsp://$RTSP_HOST:8554/stream/<n>
#
# -re          pace at real time (one second of video per second)
# -stream_loop -1   loop forever, which reproduces the hard scene cut the
#                   organiser warns about at the loop point
# -c copy      no re-encode, so the original codec is preserved: put one HEVC
#              file in ./media to exercise the mixed H.264/H.265 path

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
             -re -stream_loop -1 -i "$f" \
             -an -c copy \
             -f rtsp -rtsp_transport tcp "$url"
      echo "[publisher] stream/${n} died, restarting in 2s"
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
