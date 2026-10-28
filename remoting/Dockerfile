# Legacy .NET Remoting backends (reservation broker, Kiosk Services MessageService) on Mono.
# Build context: the repository root (rb.ps1 builds it with -f backend/remoting/Dockerfile); only the files
# below are sent (see Dockerfile.dockerignore).
FROM mono:6.12

COPY src/KioskEngine/Redbox.Services.KioskBrokerServices.KioskShared /src/KioskShared
COPY src/KioskServices/Redbox.KioskServices.ComponentModel /src/KioskServicesComponentModel
COPY lib/GenuineChannels/GenuineChannels.dll /src/lib/GenuineChannels.dll
COPY backend/remoting/src /src/host
COPY backend/remoting/build.sh /src/build.sh
COPY backend/remoting/tools /src/tools

RUN sh /src/build.sh

WORKDIR /opt/remoting
EXPOSE 7890 8090 8091
CMD ["mono", "--debug", "/opt/remoting/RemotingHost.exe"]
