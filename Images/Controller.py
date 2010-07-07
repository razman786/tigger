# -*- coding: utf-8 -*-
from PyQt4.Qt import *
from PyQt4.Qwt5 import *
import math
import numpy
import os.path
import time
import traceback
import sys
from scipy.ndimage import measurements

import Kittens.utils
from Kittens.utils import curry,PersistentCurrier
from Kittens.widgets import BusyIndicator

_verbosity = Kittens.utils.verbosity(name="imagectl");
dprint = _verbosity.dprint;
dprintf = _verbosity.dprintf;

from Images import SkyImage,Colormaps
from Models import ModelClasses,PlotStyles
from Coordinates import Projection
from Models.SkyModel import SkyModel
from Tigger import pixmaps
from Tigger.Widgets import FloatValidator

from RenderControl import RenderControl
from ControlDialog import ImageControlDialog

class ImageController (QFrame):
  """An ImageController is a widget for controlling the display of one image.
  It can emit the following signals from the image:
  raise                     raise button was clicked
  center                  center-on-image option was selected
  unload                  unload option was selected
  slice                     image slice has changed, need to redraw (emitted by SkyImage automatically)
  repaint                 image display range or colormap has changed, need to redraw (emitted by SkyImage automatically)
  """;
  def __init__ (self,image,parent,imgman,name=None):
    QFrame.__init__(self,parent);
    self.setFrameStyle(QFrame.StyledPanel|QFrame.Raised);
    # init state
    self.image = image;
    self._imgman = imgman;
    self._currier = PersistentCurrier();
    self._control_dialog = None;
    # create widgets
    self._lo = lo = QHBoxLayout(self);
    lo.setContentsMargins(0,0,0,0);
    lo.setSpacing(2);
    # raise button
    self._wraise = QToolButton(self);
    lo.addWidget(self._wraise);
    self._wraise.setIcon(pixmaps.raise_up.icon());
    self._wraise.setAutoRaise(True);
    self._can_raise = False;
    QObject.connect(self._wraise,SIGNAL("clicked()"),self._raiseButtonPressed);
    self._wraise.setToolTip("""<P>Click here to raise this image above other images. Click on the down-arrow to
      show a menu of image operations.</P>""");
    # center label
    self._wcenter = QLabel(self);
    self._wcenter.setPixmap(pixmaps.center_image.pm());
    self._wcenter.setToolTip("<P>The plot is currently centered on (the reference pixel %d,%d) of this image.</P>"%self.image.referencePixel());
    lo.addWidget(self._wcenter);
    # name/filename label
    self.name = os.path.basename(name or image.name);
    self._wlabel = QLabel(self.name,self);
    self._wlabel.setToolTip("%s %s"%(image.filename,u"\u00D7".join(map(str,image.data().shape))));
    lo.addWidget(self._wlabel,1);
    # selectors for extra axes
    self._wslicers = [None]*image.numExtraAxes();
    self._current_slice = [0]*image.numExtraAxes();
    self._has_slicing = False;
    for i in range(image.numExtraAxes()):
      iaxis,axisname,labels = image.extraAxisNumberNameLabels(i);
      if axisname.upper() not in ["STOKES","COMPLEX"]:
        lbl = QLabel("%s:"%axisname,self);
        lo.addWidget(lbl);
      else:
        lbl = None;
      slicer = QComboBox(self);
      lo.addWidget(slicer);
      slicer.addItems(labels);
      slicer.setToolTip("""<P>Selects current slice along the %s axis.</P>"""%axisname);
      QObject.connect(slicer,SIGNAL("currentIndexChanged(int)"),self._currier.curry(self.changeSlice,i));
      self._wslicers[i] = slicer;
      # hide slicer if axis <2
      if len(labels) < 2:
        lbl and lbl.hide();
        slicer.hide();
      else:
        self._has_slicing = True;
    # render control
    self._rc = RenderControl(image,self);
    QObject.connect(self._rc,SIGNAL("displayRangeChanged"),self._updateDisplayRange);
    # min/max display ranges
    lo.addSpacing(5);
    self._wrangelbl = QLabel(self);
    lo.addWidget(self._wrangelbl);
    self._minmaxvalidator = FloatValidator(self);
    self._wmin = QLineEdit(self);
    self._wmax = QLineEdit(self);
    width = self._wmin.fontMetrics().width("1.234567e-05");
    for w in self._wmin,self._wmax:
      lo.addWidget(w,0);
      w.setValidator(self._minmaxvalidator);
      w.setMaximumWidth(width);
      w.setMinimumWidth(width);
      QObject.connect(w,SIGNAL("editingFinished()"),self._changeDisplayRange);
    self._updateDisplayRange(*self._rc.displayRange());
    # full-range button
    self._wfullrange = QToolButton(self);
    lo.addWidget(self._wfullrange);
    self._wfullrange.setIcon(pixmaps.colours.icon());
    self._wfullrange.setAutoRaise(True);
    self._wfullrange.setToolTip("""<P>Click for colourmap and intensity policy options.</P>""");
    self._wraise.setToolTip("""<P>Click here to show render controls for this image.</P>""");
    QObject.connect(self._wfullrange,SIGNAL("clicked()"),self.showRenderControls);
    if not self._has_slicing:
      tooltip = """<P>You can change the currently displayed intensity range by entering low and high limits here.</P>
      <TABLE>
        <TR><TD><NOBR>Image min:</NOBR></TD><TD>%g</TD><TD>max:</TD><TD>%g</TD></TR>
        </TABLE>"""%self.image.imageMinMax();
      for w in self._wmin,self._wmax,self._wrangelbl:
        w.setToolTip(tooltip);
#      self._wfullrange.setToolTip("""<P>Click to reset the display range to the image min/max, or click on the down-arrow for more options.</P>"""+tooltip);
#      QObject.connect(self._wfullrange,SIGNAL("clicked()"),self.setFullDisplayRange);
#    else:
#      QObject.connect(self._wfullrange,SIGNAL("clicked()"),self.setSliceDisplayRange);

    # create image operations menu
    self._menu = QMenu(self.name,self);
    self._qa_raise = self._menu.addAction(pixmaps.raise_up.icon(),"Raise image",self._currier.curry(self.image.emit,SIGNAL("raise")));
    self._qa_center = self._menu.addAction(pixmaps.center_image.icon(),"Center plot on image",self._currier.curry(self.image.emit,SIGNAL("center")));
    self._qa_show_rc = self._menu.addAction(pixmaps.colours.icon(),"Colours && Intensities...",self.showRenderControls);
    self._menu.addAction("Unload image",self._currier.curry(self.image.emit,SIGNAL("unload")));
    self._wraise.setMenu(self._menu);
    self._wraise.setPopupMode(QToolButton.MenuButtonPopup);

    # init image for plotting
    self._image_border = self._image_label = None;

  def close (self):
    if self._control_dialog:
      self._control_dialog.close();
      self._control_dialog = None;

  def __del__ (self):
    self.close();

  def __eq__ (self,other):
    return self is other;

  def renderControl (self):
    return self._rc;

  def getMenu (self):
    return self._menu;

  def getFilename (self):
    return self.image.filename;

  def setName (self,name):
    self.name = name;
    self._wlabel.setText(name);

  def setPlotProjection (self,proj):
    self.image.setPlotProjection(proj);
    sameproj = proj == self.image.projection;
    self._wcenter.setVisible(sameproj);
    self._qa_center.setVisible(not sameproj);

  def addPlotBorder (self,border_pen,label,label_color=None,bg_brush=None):
    # make plot items for image frame
    # make curve for image borders
    (l0,l1),(m0,m1) = self.image.getExtents();
    self._border_pen = QPen(border_pen);
    self._image_border = QwtPlotCurve();
    self._image_border.setData([l0,l0,l1,l1,l0],[m0,m1,m1,m0,m0]);
    self._image_border.setPen(self._border_pen);
    self._image_border.setZ(self.image.z()+1);
    if label:
      self._image_label = QwtPlotMarker();
      self._image_label_text = text = QwtText(" %s "%label);
      text.setColor(label_color);
      text.setBackgroundBrush(bg_brush);
      self._image_label.setValue(l1,m1);
      self._image_label.setLabel(text);
      self._image_label.setLabelAlignment(Qt.AlignRight|Qt.AlignVCenter);
      self._image_label.setZ(self.image.z()+2);

  def setPlotBorderStyle (self,border_color=None,label_color=None):
    if border_color:
      self._border_pen.setColor(border_color);
      self._image_border.setPen(self._border_pen);
    if label_color:
      self._image_label_text.setColor(label_color);
      self._image_label.setLabel(self._image_label_text);

  def showPlotBorder (self,show=True):
    self._image_border.setVisible(show);
    self._image_label.setVisible(show);

  def attachToPlot (self,plot):
    for item in self.image,self._image_border,self._image_label:
      if item and item.plot() != plot:
        item.attach(plot);

  def setImageVisible (self,visible):
    self.image.setVisible(visible);

  def showRenderControls (self):
    if not self._control_dialog:
      self._control_dialog = ImageControlDialog(self,self._rc,self._imgman);
    if not self._control_dialog.isVisible():
      self._control_dialog.show();
    else:
      self._control_dialog.hide();

  def _updateDisplayRange (self,dmin,dmax):
    """Updates display range widgets.""";
    self._wmin.setText("%.4g"%dmin);
    self._wmax.setText("%.4g"%dmax);

  def _changeDisplayRange (self):
    """Gets display range from widgets and updates the image with it.""";
    try:
      newrange = float(str(self._wmin.text())),float(str(self._wmax.text()));
    except ValueError:
      return;
    self._rc.setDisplayRange(*newrange);

  def currentSlice (self):
    return self._rc.currentSlice();

  def changeSlice (self,iaxis,index):
    sl = list(self._rc.currentSlice());
    sl[iaxis] = index;
    self._rc.selectSlice(sl);

  def incrementSlice (self,iaxis,incr):
    sl = self._rc.currentSlice();
    slicer = self._wslicers[iaxis];
    slicer.setCurrentIndex((sl[iaxis]+incr)%slicer.count());

  def setZ (self,z,top=False,depthlabel=None,can_raise=True):
    for i,elem in enumerate((self.image,self._image_border,self._image_label)):
      if elem:
        elem.setZ(z+i);
    # set the depth label, if any
    label = self.name;
    # label = "%s %s"%(depthlabel,self.name) if depthlabel else self.name;
    if top:
      label = "<B>%s</B>"%label;
    self._wlabel.setText(label);
    # set hotkey
    self._qa_show_rc.setShortcut(Qt.Key_F9 if top else QKeySequence());
    # set raise control
    self._can_raise = can_raise;
    self._qa_raise.setVisible(can_raise);
    if can_raise:
      self._wraise.setToolTip("<P>Click here to raise this image to the top. Click on the down-arrow to access the image menu.</P>");
    else:
      self._wraise.setToolTip("<P>Click to access the image menu.</P>");

  def _raiseButtonPressed (self):
    if self._can_raise:
      self.image.emit(SIGNAL("raise"));
    else:
      self._wraise.showMenu();

