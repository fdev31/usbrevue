#!/usr/bin/env python
#
# Copyright (C) 2011 Austin Leirvik <aua at pdx.edu>
# Copyright (C) 2011 Wil Cooley <wcooley at pdx.edu>
# Copyright (C) 2011 Joanne McBride <jirab21@yahoo.com>
# Copyright (C) 2011 Danny Aley <danny.aley@gmail.com>
# Copyright (C) 2011 Erich Ulmer <blurrymadness@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#


import sys
import usb.core
import usb.util
import pcapy
from usbrevue import Packet
import optparse
import traceback
import time
import math
import signal
from threading import Thread

# Defaults for capstone14.cs.pdx.edu USB keyboard 
VENDOR_ID = 0x413c    # Dell Computer Corp.
PRODUCT_ID = 0x2105   # Model L100 Keyboard
LOGICAL_CFG = 0       
LOGICAL_IFACE = 0     
LOGICAL_ALT_SETTING = 0
DEBUG = True



class Timing(object):
    """ 
    Class to ensure timing of each packet processed matches timing of
    packets being replayed.  Timing information is based on each packet's
    timestamp.
    """
      
    def __init__(self, max_wait=5, debug=False):
        self.prev_ts = None
        self.max_wait = max_wait
        self.debug = debug

    def wait_relative(self, ts_sec, ts_usec):
        """
        On first call, returns instantly. On subsequent calls, waits for the
        difference between this call's timestamp and the previous call's
        timestamp.
        """
        ts = ts_sec + ts_usec/1e6
        if self.prev_ts is not None:
            self.sleep(ts - self.prev_ts)
        self.prev_ts = ts

    def sleep(self, dur):
        """
        Sleep for dur seconds. Returns instantly if dur < 0, and won't
        sleep for longer than self.max_wait. 
        """
        now = time.time()
        until = now + min(dur, self.max_wait)
        if self.debug:
            sys.stderr.write("\nwaiting for %s" % (until - now))
        while now < until:
            time.sleep(until-now)
            now = time.time()



class PollThread(Thread):
    def __init__(self, ep):
        Thread.__init__(self)
        self.ep = ep

    def run(self):
        while 1:
            try:
                r = self.ep.read(self.ep.wMaxPacketSize)
                print ' '.join(map(lambda b: "%02x" % b, r))
            except usb.core.USBError as e:
                if str(e) == "Operation timed out":
                    # there must be a better way
                    continue
                sys.stderr.write('\n%s' % e)
                break



class Replayer(object):
    """
    Class that encapsulates functionality to replay pcap steam packets 
    to a USB device.
    """
    def __init__(self, vid=VENDOR_ID, pid=PRODUCT_ID, logical_cfg=0, logical_iface=0, logical_alt_setting=0, infile='-', debug=DEBUG):
        """
        Constructor to initialize to the various usb fields, such as vendor
        id, product id, configuration, interface, alternate setting and 
        endpoint address.  Based on vendor and product id, it will get
        the USB device and set the appropriate parameters.
        """
        self.debug = debug
        if self.debug: sys.stderr.write('\nIn Replayer.__init__')
        self.init_handlers()
        self.urbs = []
        self.vid = vid
        self.pid = pid

        if self.debug: sys.stderr.write('\nIn Replayer.__init__ 1')
        self.device = self.get_usb_device(self.vid, self.pid)
        if self.debug: sys.stderr.write('\nIn Replayer.__init__ 2')
        if self.device is None:
            raise ValueError('The device with vid=0x%x, pid=0x%x is not connected') % (self.vid, self.pid)

        if self.debug: sys.stderr.write('\nIn Replayer.__init__ 3')
        self.logical_cfg = logical_cfg
        self.set_configuration(self.logical_cfg)
        self.logical_iface = logical_iface
        if self.debug: sys.stderr.write('\nIn Replayer.__init__ 4')

        if self.device.is_kernel_driver_active(self.logical_iface):
            if self.debug: sys.stderr.write( '\nDetaching kernal driver')
            self.device.detach_kernel_driver(self.logical_iface)
        if self.debug: sys.stderr.write( '\nIn Replayer.__init__ 5')

        self.logical_alt_setting = logical_alt_setting
        self.set_interface(self.logical_iface, self.logical_alt_setting)
        if self.debug: sys.stderr.write( '\nIn Replayer.__init__ 6')

        if self.debug:
            self.print_descriptor_info()
        if self.debug: sys.stderr.write( '\nIn Replayer.__init__ 7')

        # sort all endpoints on our interface: incoming interrupts go into 
        # poll_eps, and all others go into eps, indexed by epnum
        self.poll_eps = []
        self.eps = {0x00: None, 0x80: None}
        for ep in self.iface:
            if self.debug: sys.stderr.write( '\nIn Replayer.__init__ 8')
            if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN and \
                usb.util.endpoint_type(ep.bmAttributes) == usb.util.ENDPOINT_TYPE_INTR:
                if self.debug: sys.stderr.write( '\nIn Replayer.__init__ 9')
                if self.debug: sys.stderr.write( '\nIn Replayer.__init__ 10')
                self.poll_eps.append(ep)
                if self.debug: sys.stderr.write( '\nIn Replayer.__init__ 11')
            else:
                if self.debug: sys.stderr.write( '\nIn Replayer.__init__ 12')
                self.eps[ep.bEndpointAddress] = ep
                if self.debug: sys.stderr.write( '\nIn Replayer.__init__ 13')

        self.timer = Timing(debug=self.debug)
        if self.debug: sys.stderr.write( '\nIn Replayer.__init__ 14')


    def get_logical_cfg(self):
        """ Get the logical configuration index for this device """
        if self.debug: sys.stderr.write( '\nIn Replayer.get_logical_cfg')
        self.logical_cfg = self.get_logical_cfg()

    def reset_device(self):
        """ 
        Reset device and endpoint address.
        """
        if self.debug:
            sys.stderr.write( '\nIn Replayer.reset_device: resetting device')
        self.device.reset()
        res = self.device.is_kernel_driver_active(self.logical_iface)
        if not res:
            sys.stderr.write( 'Re-attaching kernal driver')
            self.device.attach_kernel_driver(self.logical_iface)


    def print_all(self):
        """ Print all usb descriptors """
        sys.stderr.write( '\n\nDevice enumeration tree ...')
        self.print_device_enumeration_tree()
        sys.stderr.write( '\nDevice descriptor fields ...')
        self.print_device_descriptor_fields()
        sys.stderr.write( '\nConfiguration descriptor fields ...')
        self.print_cfg_descriptor_fields()
        sys.stderr.write( '\nInterface descriptor fields ...')
        self.print_iface_descriptor_fields()
        sys.stderr.write( '\nEndpoint descriptor fields ...')
        self.print_ep_descriptor_fields(self.poll_eps[0])


    # Execute lsusb to see USB devices on your system
    #
    # Descriptor class hierarchy is:
    #                                      device
    #                                   /          \
    #                      configuration            configuration
    #                     /             \
    #          interface                 interface        ...
    #         /         \
    #  endpoint         endpoint            ...           ...
    #
    #
    # Device descriptor represents entire device.
    def get_usb_device(self, vid=VENDOR_ID, pid=PRODUCT_ID):
        """ 
        Get the usb.core.Device object based on vendorId and productId.  
        """
        if self.debug:
            sys.stderr.write( '\nIn Replayer.get_usb_device')
        device = usb.core.find(idVendor=vid, idProduct=pid)
        if device is None:
            raise ValueError('USB Device with vendorId', vid, ', and productId', pid, 'not found')
        return device
      

    # Configuration descriptor specifies values such as amount of power
    # this particular configuration uses, if device is self or bus 
    # powered and number of interfaces it has. Few devices have more
    # than 1 configuration, so cfg index will usually be empty and we will
    # simply select the only active configuration at index 0. Only 1 
    # configuration may be enabled at a time
    def set_configuration(self, logical_cfg=0):
        """ 
        Set configuration descriptor based on the cfg_logical_idx for desired 
        configuration in the hierarchy. 
        Example to set the second configuration:  
          config = dev[1]
        """
        if self.debug:
            sys.stderr.write( '\nIn Replayer.set_configuration')
        self.cfg = self.device[logical_cfg]


    # Interface descriptor resolves into a functional group performing a
    # single feature of the device.  For example, you could have an 
    # all-in-one printer, where interface 1 describes the endpoints of a
    # fax function, interface 2 describes the endpoints of a scanner 
    # function, and interface 3 describes the endpoints of a printer 
    # function.  More than one interface may be enabled at one time.  The
    # altsetting function will allow the interface to change settings on
    # the fly.  
    # For example, interface 0 could have an altsetting of 0, and interface
    # 1 could have an altsetting of 0 or an altsetting of 1.  A 
    # set_interface request can be used to enable one or the other of those
    # interface descriptors.  If interface 1, altsetting 1 is set, then
    # we can change the endpoint settings of interface 1, altsetting 1 
    # without affecting the endpoint settings of interface 1, altsetting 0.
    def set_interface(self, logical_iface=0, logical_alt_setting=0):
        """ 
        Set interface descriptor based on interface and alternate setting 
        numbers.  Example to access the first interface and first alternate 
        setting:  
          iface = cfg[(0,0)]
        """
        if self.debug:
            sys.stderr.write( '\nIn Replayer.set_interface')
        self.iface = self.cfg[(logical_iface, logical_alt_setting)]
        try:
            self.device.set_interface_altsetting(self.iface)
        except usb.core.USBError as e:
            sys.stderr.write( '\nIn Replayer.set_interface: %s ' % e)
            sys.stderr.write("Error trying to set interface alternate setting")
            pass


    def run(self, pcap, out):
        """
        Run the replayer loop.  The loop will get each consecutive pcap 
        packet and replay it as nearly as possible.
        """
        if self.debug: sys.stderr.write( '\nIn Replayer.run')
        loops = 0
        if self.debug: self.print_all()

        for ep in self.poll_eps:
            if self.debug: sys.stderr.write( 'Spawning poll thread for endpoint 0x%x' % ep.bEndpointAddress)
            thread = PollThread(ep)
            thread.start()

        if self.debug:
            sys.stderr.write( 'Entering Replayer run loop')
        while True:
            try:
                if self.debug:
                    sys.stderr.write( '------------------------------------------')
                    sys.stderr.write( '\nIn Replayer.run: Starting loop: %d ' % loops)
                loops += 1
                hdr, pack = pcap.next()
                if hdr is None:
                    break # EOF

                packet = Packet(hdr, pack)
                if self.debug:
                    #print '\nIn run: Dumping pcap packet data ...'
                    #out.dump(hdr, packet.repack())
                    sys.stderr.write( '\nIn Replayer.run: Printing pcap field info...')
                    packet.print_pcap_fields()
                    sys.stderr.write( '\nIn Replayer.run: Printing pcap summary info...')
                    packet.print_pcap_summary()

                # Wait for awhile before sending next usb packet
                self.timer.wait_relative(packet.ts_sec, packet.ts_usec)
                self.send_usb_packet(packet)
            except Exception:
                sys.stderr.write("Error occured in replayer run loop. Here's the traceback")
                traceback.print_exc()
                break
        #wait for keyboard interrupt, let poll threads continue
        while True:
            time.sleep(5)


    def send_usb_packet(self, packet):
        """ 
        Send the usb packet to the device.  It will be either a control packet
        with IN or OUT direction (with respect to the host), or a non-control
        packet (Bulk, Isochronous, or Interrupt) with IN or OUT direction.
        It can also be a submission from the host or a callback from the 
        device.  In any case, there may or may not be a data payload.
        """
        if self.debug:
            sys.stderr.write( '\nIn Replayer.send_usb_packet')

        # Check to see if this is a setup packet.
        if packet.is_setup_packet:
            self.send_setup_packet(packet)
        else:
            # Check that packet is on an endpoint we care about
            if packet.epnum not in self.eps:
                if self.debug: sys.stderr.write( "Ignoring endpoint %s" % hex(packet.epnum))
                return

            ep = self.eps[packet.epnum]

            # Otherwise check to see if it is a submission packet.
            # Submission means xfer from host to USB device.
            if packet.event_type == 'S':       
                self.send_submission_packet(packet, ep)

            # Otherwise check to see it it is a callback packet.
            # Callback means xfer from USB device to host.
            elif packet.event_type == 'C':   
                self.get_callback(packet)


    def send_setup_packet(self, packet):
        """ Send the usb setup packet. """
        if self.debug:
            sys.stderr.write( '\nIn Replayer.send_usb_packet: this is a setup packet with urb id = 0x%x' % packet.urb)
        #if packet.urb not in self.urbs:
        if self.debug:
            sys.stderr.write( '\nAppending 0x%x to urb list' % packet.urb)
        self.urbs.append(packet.urb)
        if self.debug:
            sys.stderr.write( '\nCurrent urbs = %s' % self.urbs)

        # IN means read bytes from device to host.
        if (packet.epnum == 0x80):
            self.ctrl_transfer_from_device(packet)
        # OUT means write bytes from host to device.
        elif (packet.epnum == 0x00):
            self.ctrl_transfer_to_device(packet)


    def ctrl_transfer_to_device(self, packet):
        """ Send the control transfer packet from the host to the device. """
        # If no data payload then send_array should be None
        if self.debug:
            sys.stderr.write( '\nIn Replayer.send_usb_packet: Setup packet direction is OUT - writing from host to device for packet urb id = 0x%x' % packet.urb)
        numbytes = self.device.ctrl_transfer(packet.setup.bmRequestType, packet.setup.bRequest, packet.setup.wValue, packet.setup.wIndex, packet.data)
        if numbytes != len(packet.data):
            sys.stderr.write( '\nError: %d bytes sent in OUT control transfer out of %d bytes we attempted to send' % (numbytes, len(packet.data)))


    def ctrl_transfer_from_device(self, packet):
        """ Send the control transfer packet from the device to the host. """
        # If no data payload then numbytes should be 0
        if self.debug:
            sys.stderr.write( '\nIn Replayer.ctrl_transfer_from_device')
            sys.stderr.write( '\nIN direction (reading bytes from device to host for packet urb id = 0x%x' % packet.urb)
        ret_array = self.device.ctrl_transfer(packet.setup.bmRequestType, packet.setup.bRequest, packet.setup.wValue, packet.setup.wIndex, packet.setup.wLength)
        if len(ret_array) != packet.length:
            sys.stderr.write( '\nError: %d bytes read in IN control transfer out of %d bytes we attempted to read' % (len(ret_array), packet.length))
            sys.stderr.write('\nret_array in ctrl_transfer_from_device is %s' % ret_array)

    def send_submission_packet(self, packet, ep):
        """ Send the submission packet. """
        # Otherwise check to see if it is a submission packet.
        # A submission can be either a read or write so check direction.
        # Submission means xfer from host to USB device.
        if self.debug:
            sys.stderr.write( '\nIn Replayer.send_submission_packet: this is a submission packet for urb id = 0x%x' % packet.urb)
        #if packet.urb not in self.urbs:
        if self.debug:
            sys.stderr.write( '\nAppending 0x%x to urb list' % packet.urb)
        self.urbs.append(packet.urb)
        if self.debug:
            sys.stderr.write( '\nCurrent urbs =  %s' % self.urbs)

        if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN:
            self.read_from_device(packet, ep)
        else:
            self.write_to_device(packet, ep)


    def write_to_device(self, packet, ep):
        """ Write the packet to the devices endpoint. """
        if self.debug:
            sys.stderr.write( '\nIn Replayer.write_to_device')
        numbytes = 0  
        if packet.data:
            numbytes = ep.write(packet.data)
        else:
            if self.debug:
                sys.stderr.write( '\nPacket data is empty.  No submission data to send.')
        if self.debug:
            sys.stderr.write( '\nWrote %d submission bytes to USB device, expected to write %d bytes ' %(numbytes, packet.len_cap))
        if numbytes != packet.len_cap:
            sys.stderr.write( '\nError: %d bytes sent in submission (transfer to USB device) out of %d bytes we attempted to send' % (numbytes, packet.len_cap))


    def read_from_device(self, packet, ep):
        """ Read the packet from the devices endpoint. """
        if self.debug:
            sys.stderr.write( '\nIn Replayer.read_from_device')
        TIMEOUT = 1000
        ret_array = []
        if self.debug:
            sys.stderr.write( '\nAttempting to read %d bytes from device to host' % packet.length)
        if packet.length:
            try:
                ret_array = ep.read(packet.length, TIMEOUT)
                print 'ret_array for callback is %s' % ret_array
            except usb.core.USBError as e:
                sys.stderr.write( '\nIn Replayer.read_from_device: %s' % e)

            if self.debug:
                sys.stderr.write( '\nFinished attempting to read %d callback bytes from device to host' % packet.length)
                sys.stderr.write( '\nActually read %d callback bytes from device to host' % len(ret_array))
            if ret_array:
                if self.debug:
                    sys.stderr.write( '\n%d data items read from callback packet.  Data = %s' % (len(ret_array), ret_array))
                if packet.length != len(ret_array):
                    sys.stderr.write( '\nError: %d bytes sent in submission (transfer to USB device) out of %d bytes we attempted to send' % (packet.length, len(ret_array)))
            else:
                if self.debug:
                    sys.stderr.write( '\nNo data items were read from callback packet.  ')
        else:
            if self.debug:
                sys.stderr.write( '\nNo callback bytes to read: length of packet.data is 0')
            

    def get_callback(self, packet):
        """ 
        Get the callback.  This doesn't really do anything since the
        replayer can't really do a callback, so the callback is simulated ???
        """
        if self.debug:
            sys.stderr.write( '\nIn Replayer.get_callback: this is a callback packet for urb id = 0x%x' % packet.urb)
        if packet.urb in self.urbs:
            if self.debug:
                sys.stderr.write( '\nRemoving 0x%x from urb list' % packet.urb)
            self.urbs.remove(packet.urb)
        else:
            sys.stderr.write( '\nPacket urb id=0x%x has a callback but not a submission' % packet.urb)
        if self.debug:
            sys.stderr.write( '\nCurrent urbs = %s' % self.urbs)


    def keyboard_handler(self, signum, frame):
        if self.debug:
            sys.stderr.write( '\nIn Replayer.keyboard_handler')
        """ Keyboard handler will also reset the current USB device """
        sys.stderr.write( '\nSignal handler called with signal %d' % signum)
        self.reset_device()
        sys.exit(0)


    def init_handlers(self):
        """ 
        Initialize keyboard handler. Use CNTL-C to exit replayer program 
        """
        if self.debug:
            sys.stderr.write( '\nIn Replayer.init_handlers 1')
        try:
            signal.signal(signal.SIGINT, self.keyboard_handler)
            if self.debug:
                sys.stderr.write( '\nIn Replayer.init_handlers 2')
        except usb.core.USBError as e:
            sys.stderr.write( '\nIn Replayer.init_handlers: %s' % e)
            pass

        if self.debug:
            sys.stderr.write( '\nIn Replayer.init_handlers 3')
      


    def print_descriptor_info(self):
        """ 
        Print out some basic device hierarcy numbers, ids, info.
        """
        sys.stderr.write( '\nvid = 0x%x' % self.vid)
        sys.stderr.write( '\npid = 0x%x' % self.pid)
        sys.stderr.write( '\nlogical_cfg_idx = %d' % self.logical_cfg)
        sys.stderr.write( '\nlogical_iface_idx = %d' % self.logical_iface)
        sys.stderr.write( '\nlogical_alt_setting_idx = %d' % self.logical_alt_setting)
      

    # Got this from USB in a NutShell, chp 5.
    def print_device_descriptor_fields(self):
        """ 
        Print out all device descriptor fields.
        """
        dev = self.device
        # bLength = Size of device descriptor in bytes (18 bytes) (number).
        sys.stderr.write( '\nbLength = %d' % dev.bLength)
        # bDescriptorType = Device descriptor (0x01) in bytes (constant).
        sys.stderr.write( '\nbDescriptorType = 0x%x' % dev.bDescriptorType)
        # bDescriptorType = USB spec number which device complies to (bcd). 
        sys.stderr.write( '\nbcdUSB = 0x%x' % dev.bcdUSB)
        # bDeviceClass = Class code assigned by USB org (0 or 0xff) (class).
        sys.stderr.write( '\nbDeviceClass = 0x%x' % dev.bDeviceClass)
        # bDeviceSubClass = Sublass code assigned by USB org (subClass).
        sys.stderr.write( '\nbDeviceSubClass = 0x%x' % dev.bDeviceSubClass)
        # bDeviceProtocol = Protocol code assigned by USB org (protocol).
        sys.stderr.write( '\nbDeviceProtocol = 0x%x' % dev.bDeviceProtocol)
        #bMaxPacketSize = Max packet size for zero endpoint (8, 16, 32, 64).
        #print 'bMaxPacketSize = ', dev.bMaxPacketSize
        # idVendor = Vendor ID assigned by USB org (ID).
        sys.stderr.write( '\nidVendor = 0x%x' % dev.idVendor)
        # idProduct = Product ID assigned by USB org (ID).
        sys.stderr.write( '\nidProduct = 0x%x' % dev.idProduct)
        # bcdDevice = Device release number (bcd).
        sys.stderr.write( '\nbcdDevice = 0x%x' % dev.bcdDevice)
        # iManufacturer = Index of manufacturer string descriptor (index).
        if dev.iManufacturer is not None:
            sys.stderr.write( '\niManufacturer = 0x%x' % dev.iManufacturer)
        # iProduct = Index of product string descriptor (index).
        if dev.iProduct is not None:
            sys.stderr.write( '\niProduct = 0x%x' % dev.iProduct)
        # iSerialNumber = Index of serial number string descriptor (index).
        if dev.iSerialNumber is not None:
            sys.stderr.write( '\niSerialNumber = 0x%x' % dev.iSerialNumber)
        # iNumConfigurations = Number of possible configurations (integer).
        sys.stderr.write( '\nbNumConfigurations = 0x%x' % dev.bNumConfigurations)


    # Got this from USB in a NutShell, chp 5.
    def print_cfg_descriptor_fields(self):
        """ 
        Print out all configuration descriptor fields.
        """
        cfg = self.cfg
        # bLength = Size of configuration descriptor in bytes (number).
        sys.stderr.write( '\nbLength = %d' % cfg.bLength)
        # bDescriptorType = Configuration descriptor (0x02) in bytes (constant).
        sys.stderr.write( '\nbDescriptorType = 0x%x' % cfg.bDescriptorType)
        # bTotalLength = Total length in bytes of data returned (number). 
        # This indicates the number of bytes in the configuration hierarchy.
        sys.stderr.write( '\nwTotalLength = %d' % cfg.wTotalLength)
        # bNumInterfaces = Total length in bytes of data returned (number). 
        sys.stderr.write( '\nbNumInterfaces = %d' % cfg.bNumInterfaces)
        # bConfigurationValue = Value to use as arg to select this cfg (number).
        sys.stderr.write( '\nbConfigurationValue = %d' % cfg.bConfigurationValue)
        # iConfiguration = Index of string descriptor describing this cfg (index).
        # This string is in human readable form.
        sys.stderr.write( '\niConfiguration = %d' % cfg.iConfiguration)
        # bmAttributes = Bus or self powered, remote wakeup or reserved (bitmap). 
        # Remote wakeup allows device to wake up the host when the host is in suspend.
        sys.stderr.write( '\nbmAttributes = %d' % cfg.bmAttributes)
        # bMaxPower = Maximum power consumption in 2mA units (mA).
        sys.stderr.write( '\nbMaxPower = %d' % cfg.bMaxPower)


    # Got this from USB in a NutShell, chp 5.
    # Interface descriptor is a grouping of endpoints into a functional 
    # group performing a single feature of the device.
    def print_iface_descriptor_fields(self):
        """ 
        Print out all interface descriptor fields.
        """
        iface = self.iface
        # bLength = Size of interface descriptor in bytes (number).
        sys.stderr.write( '\nbLength = %d' % iface.bLength)
        # bDescriptorType = Interface descriptor (0x04) in bytes (constant).
        sys.stderr.write( '\nbDescriptorType = %d' % iface.bDescriptorType)
        # bInterfaceNumber = Number of interface (number).
        sys.stderr.write( '\nbInterfaceNumber = %d' % iface.bInterfaceNumber)
        # bAlternateSetting = Value used to select alternate setting (number).
        sys.stderr.write( '\nbAlternateSetting = %d' % iface.bAlternateSetting)
        # bNumEndpoints = Number of endpoints used for this interface (number).
        # This excludes endpoint 0.
        sys.stderr.write( '\nbNumEndpoints = %d' % iface.bNumEndpoints)
        # bInterfaceClass = Class code assigned by USB org (class).
        # Class could be HID, communicatins, mass storage, etc.
        sys.stderr.write( '\nbInterfaceClass = %d' % iface.bInterfaceClass)
        # bInterfaceSubClass = SubClass code assigned by USB org (subClass).
        sys.stderr.write( '\nbInterfaceSubClass = %d' % iface.bInterfaceSubClass)
        # bInterfaceProtocol = Protocol code assigned by USB org (protocol).
        sys.stderr.write( '\nbInterfaceProtocol = %d' % iface.bInterfaceProtocol)
        # iInterface = Index of string descriptor describing this interface (index).
        sys.stderr.write( '\niInterface = %d' % iface.iInterface)


    # Got this from USB in a NutShell, chp 5.
    def print_ep_descriptor_fields (self, ep):
        """ 
        Print out all endpoint descriptor fields.
        """
        # bLength = Size of endpoint descriptor in bytes (number).
        sys.stderr.write( '\nbLength = %d' % ep.bLength)
        # bDescriptorType = Endpoint descriptor (0x05) in bytes (constant).
        sys.stderr.write( '\nbDescriptorType = %d' % ep.bDescriptorType)
        # bEndpointAddress = Endpoint descriptor (0x05) in bytes (constant).
        # Bits 0-3=endpoint number, Bits 4-6=0, Bit 7=0(out) or 1(in).
        sys.stderr.write( '\nbEndpointAddress = 0x%x' % ep.bEndpointAddress)
        # Endpoint logical index is derived from Bits 0-3=endpoint number of
        # the bEndpointAddress.
        sys.stderr.write( '\nendpoint logical index = 0x%x' % (ep.bEndpointAddress & 0xf))
        # bmAttributes = Bits 0-1=Transfer type, other bits refer to 
        # synchronization type, usage type (iso mode) (bitmap).
        # Transfer types: 00=Control, 01=Isochronous, 10=Bulk, 11=Interrupt.
        sys.stderr.write( '\nbmAttributes = %d' % ep.bmAttributes)
        # wMaxPacketSize = Max packet size this endpoint is capable of 
        # sending or receiving (number).
        # This is the maximum payload size for this endpoint.
        sys.stderr.write( '\nwMaxPacketSize = %d' % ep.wMaxPacketSize)
        # bInterval = Interval for polling endpoint data transfers.  
        # Value in frame counts (?).  Ignored for bulk and control endpoints.
        # Isochronous must equal 1.  This field for interrupt endpoints may 
        # range from 1 to 255 (number).
        # bInterval is used to specify the polling interval of certain transfers.
        # The units are expressed in frames.  This equates to 1ms for low/full
        # speed devices, and 125us for high speed devices.
        sys.stderr.write( '\nbInterval = %d' % ep.bInterval)


    def print_device_enumeration_tree(self):
        """ 
        Print out the device enumeration, which includes
        device, configuration, interface and endpoint descriptors.
        """
        dev = self.device
        for cfg in dev:
            sys.stderr.write( '\nconfiguration value = %s ' % (cfg.bConfigurationValue))
            for iface in cfg:
                sys.stderr.write( '\n  interface number = %s ' % (iface.bInterfaceNumber))
                sys.stderr.write( '\n  alternate setting = %s ' % (iface.bAlternateSetting))
                for ep in iface:
                    sys.stderr.write( '\n    endpoint address = 0x%x ' % (ep.bEndpointAddress))



def get_arguments(argv):
    """ Get command line arguments. 
        python usbreplay.py -h will show a help message
    """

    parser = optparse.OptionParser(usage="usage: %prog [options] [filename]")

    # Get the input file stream (pcap stream or filename
    parser.add_option("-f", "--file", 
                      dest="infile", 
                      default="-",
                      help="Input pcap stream (filename or '-' for stdin)"
                     )

    # Get the vendor id
    parser.add_option('-v', '--vid', 
                      dest='vid', 
                      default=VENDOR_ID, 
                      type="int", 
                      help="The vendor id for the USB device"
                     )

    # Get the product id
    parser.add_option("-p", "--pid", 
                      dest="pid", 
                      default=PRODUCT_ID, 
                      type="int", 
                      help="The product id for the USB device"
                     )

    # Get the configuration index (defaults to 0)
    parser.add_option("-c", "--cfg", 
                      dest="logical_cfg", 
                      default=LOGICAL_CFG, 
                      type="int", 
                      help="The logical configuration index of the device (starting at 0) in the device hierarchy"
                     )

    # Get the interface index (defaults to 3)
    parser.add_option("-i", "--iface", 
                      dest="logical_iface", 
                      default=LOGICAL_IFACE, 
                      type="int", 
                      help="The logical interface index in the configuration (starting at 0) in the device hierarchy"
                     )

    # Get the alternate setting index (defaults to 0)
    parser.add_option("-a", "--altsetting", 
                      dest="logical_alt_setting", 
                      default=LOGICAL_ALT_SETTING, 
                      type="int", 
                      help="The logical alternate setting index in the interface (starting at 0) in the device hierarchy"
                     )

    # Set the debug mode to quiet or verbose
    parser.add_option("-d", "--debug", 
                      dest="debug", 
                      default=True,
                      action="store_true", 
                      help="Print debug messages to stdout"
                     )
    
    options, remaining_args = parser.parse_args()
    if options.debug:
        sys.stderr.write( '\nOptions:  %s' % options)
        sys.stderr.write( '\nRemaining_args:  %s' % remaining_args)
    return options


def print_options(options):
    """ 
    Print out command line options or their defaults.
    """

    #if options.debug:
    sys.stderr.write( '\noptions.infile = %s' % options.infile)
    sys.stderr.write( '\noptions.vid = 0x%x' % options.vid)
    sys.stderr.write( '\noptions.pid = 0x%x ' % options.pid)
    sys.stderr.write( '\noptions.cfg = %d' % options.logical_cfg)
    sys.stderr.write( '\noptions.iface = %d' % options.logical_iface)
    sys.stderr.write( '\noptions.altsetting = %d' % options.logical_alt_setting)
    sys.stderr.write( '\noptions.debug = %d' % options.debug)


if __name__ == '__main__':
    # read a pcap stream from a file or from stdin, write the contents back
    # to stdout (for debug info), convert input stream to USB packets, and 
    # send USB packets to the device or stdout.
    options = get_arguments(sys.argv)
    print_options(options)
    pcap = pcapy.open_offline(options.infile)
    out = None
    if not sys.stdout.isatty():
    	out = pcap.dump_open('-')
    replayer = Replayer(options.vid, options.pid, options.logical_cfg, options.logical_iface, options.logical_alt_setting, options.infile, options.debug)
    replayer.run(pcap, out)

