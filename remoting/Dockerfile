# Legacy .NET Remoting backends (reservation broker, Kiosk Services MessageService) on Mono.
# Build context: this repository's root (rb.ps1 builds it with -f remoting/Dockerfile). The kiosk sources and
# GenuineChannels come from the Software submodule (software/); only the files below are sent (see
# Dockerfile.dockerignore).
FROM mono:6.12

COPY software/src/KioskEngine/Redbox.Services.KioskBrokerServices.KioskShared /src/KioskShared
COPY software/src/KioskServices/Redbox.KioskServices.ComponentModel /src/KioskServicesComponentModel
COPY software/lib/GenuineChannels/GenuineChannels.dll /src/lib/GenuineChannels.dll
COPY remoting/src /src/host
COPY remoting/build.sh /src/build.sh
COPY remoting/tools /src/tools

RUN sh /src/build.sh

WORKDIR /opt/remoting
EXPOSE 7890 8090 8091
CMD ["mono", "--debug", "/opt/remoting/RemotingHost.exe"]
