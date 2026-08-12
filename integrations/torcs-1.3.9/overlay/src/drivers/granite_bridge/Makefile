ROBOT = granite_bridge
MODULE = ${ROBOT}.so
MODULEDIR = drivers/${ROBOT}

SOURCES = ${ROBOT}.cpp sensors.cpp ObstacleSensors.cpp

SHIPDIR = drivers/${ROBOT}
SHIP = ${ROBOT}.xml
SHIPSUBDIRS =
PKGSUBDIRS = ${SHIPSUBDIRS}

src-robots-base_PKGFILES = $(shell find * -maxdepth 0 -type f -print)
src-robots-base_PKGDIR = ${PACKAGE}-${VERSION}/$(subst ${TORCS_BASE},,$(shell pwd))

include ${MAKE_DEFAULT}
