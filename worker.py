from PyQt5.QtCore import *
import time
import sys
import subprocess
import re
import shutil
import pathlib

VIDEO_EXTENSIONS = [".mp4",".avi",".mkv",".3gp", ".mov"] #most used video extensions

INFO = 0  #loglevel
ERROR = 1 #loglevel
#string format: Duration: 00:02:00.92, start: 0.000000, bitrate: 10156 kb/s
durationRegex = re.compile("[ ]+Duration: (\d{2}):(\d{2}):(\d{2}.\d{2})")
#string format: frame=  361 fps= 51 q=32.0 size=    1792kB time=00:00:12.04 bitrate=1219.0kbits/s speed=1.71x
progressRegex = re.compile("frame=[ 0-9]+fps=[ 0-9\.]+q=[ 0-9\.\-]+L*size=[ 0-9]+[bBkKgGmM ]+time=(\d{2}):(\d{2}):(\d{2}.\d{2})")

class Worker(QObject):
    finished = pyqtSignal() #taskPerformer onThreadFinished() will be called
    emitLog = pyqtSignal(int, str) #emit log to taskPerformer (displayLog(i))
    emitProgress = pyqtSignal(int, int) #emit progress to taskPerformer

    proc = None
    continueWork = True
    totSize = processedSize = 0 # tot files size
    converted = copied = fails = 0

    def __init__(self, inputPath, outputPath, ffmpeg_opt, parent=None):
        super(Worker, self).__init__(parent)
        self.inputPath = pathlib.Path(inputPath)
        self.outputPath = pathlib.Path(outputPath)
        self.ffmpeg_opt = ffmpeg_opt

    @pyqtSlot()
    def operationRunner(self):
        #collecting and printing stats
        time_start = time.time() #start time
        t = time.localtime(time_start) #convert time_start in a tuple, for easily extracting hour, min, sec
        self.totSize = self.getTotalSize(self.inputPath)
        self.thick = 100/self.totSize
        self.emitLog.emit(INFO, "Launched at %d:%02d:%02d\n" %(t.tm_hour, t.tm_min, t.tm_sec))
        self.emitLog.emit(INFO, "Input path: %s\n" % str(self.inputPath))
        self.emitLog.emit(INFO, "Total input size: %0.f MB\n" % round((self.totSize/(1024*1024.0)), 2))
        self.emitLog.emit(INFO, "Output path: %s\n" % str(self.outputPath))
        self.emitLog.emit(INFO, "ffmpeg options: %s\n" % str(self.ffmpeg_opt))
        self.emitLog.emit(INFO, "-------------------------------------------------------------\n")

        self.fileManager(self.inputPath, self.outputPath)

        self.emitLog.emit(INFO, "-------------------------- Done --------------------------\n")
        sec = time.time() - time_start
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        self.emitLog.emit(INFO, "Total time: %d:%02d:%02d sec - It's now safe to close this window.\n" %(h,m,s))
        self.emitLog.emit(INFO, "Processed: %d - copied files: %d - errors: %d" % (self.converted, self.copied, self.fails))
        self.finished.emit()

    #convert file only if it's a video, otherwise copy it
    #input_file: type(input_file) = type(output_file) = pathlib.Path
    def convert_or_copy(self, input_file, output_dir):
        if self.continueWork == False:
            return
        output_name = output_dir / input_file.name
        try:
            if input_file.suffix in VIDEO_EXTENSIONS:
                self.emitLog.emit(INFO, "Converson: %s " % str(input_file))
                self.processedSize += self.convert_file(input_file, output_name, self.updProgress)
                self.converted +=1
            else:
                self.emitLog.emit(INFO, "Copy: %s " % str(input_file))
                self.processedSize += self.copy(input_file, output_name, self.updProgress)
                self.copied +=1
        except Exception as e:
            self.emitLog.emit(INFO, "- Failed")
            self.emitLog.emit(ERROR, "%s" % str(e))
            self.fails += 1
        else:
            self.emitLog.emit(INFO, "- OK\n")

    #rate: percentage of current file progress
    #fSize: current file size in bytes
    def updProgress(self, rate, fSize):
        #total progress = self.processedSize + current file processed bytes
        self.emitProgress.emit(round((100/self.totSize)*(self.processedSize+(fSize/100*rate))), rate)


    #scan all inputPath and perform operations
    def fileManager(self, inputPath, outputPath):
        if self.continueWork == False:
            return
        if inputPath == outputPath:
            self.emitLog.emit(ERROR, "ERROR!: input path is the same as output path\n")
            return
        if inputPath.is_file() and (inputPath.parent == outputPath):
            self.emitLog.emit(ERROR, "ERROR!: input and output files must be in different folders.\n")
        if not outputPath.exists():
            self.emitLog.emit(INFO, "Creating dir: %s\n" % str(outputPath))
            outputPath.mkdir()
        #input is a file, need only to convert (or copy) to new location
        if inputPath.is_file():
            self.convert_or_copy(inputPath, outputPath)
        #input is a directory
        else:
            for item in inputPath.iterdir():
                if item.is_dir():
                    destin_dir = outputPath / item.name #path concatenation
                    self.fileManager(item, destin_dir)
                else:
                    self.convert_or_copy(item, outputPath)

    #TODO: preserve input file permissions? (output file permission are different)
    #conversion of a read-only file will generate a non-readonly file.
    def convert_file(self, input_name, output_name, updProgress):
        fileSize = input_name.stat().st_size
        progress=0
        lastLine = slastLine = ""
        DQ="\"" #double quote
        #ffmpeg: sane values are between 18 and 28
        #https://trac.ffmpeg.org/wiki/Encode/H.264
        #ffmpeg -i input.mp4 -c:v libx264 -crf 26 output.mp4
        self.proc = subprocess.Popen("ffmpeg -y -loglevel info -i " + DQ + str(input_name) + DQ + " " + self.ffmpeg_opt + " " + DQ+str(output_name)+DQ,stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1, universal_newlines=True)
        while True:
            #another way is to use ffmpeg -y -progress filename ....and parse filename, but there are the same info ffmpeg print to stderr.
            sys.stderr.flush()
            #read STDERR output (only for ffmpeg, because it have the option to send video output to stdout stream, so it uses stderr for logs.)
            line=self.proc.stderr.readline()
            if line:
                slastLine = lastLine
                lastLine = line
            p = re.match(progressRegex, line)

            if p is not None:
                #reading current time interval
                hh=float(p.group(1)) #hours
                mm=float(p.group(2)) #mins
                ss=float(p.group(3)) #secs (floating point, ex: 21.95)
                progress=hh*3600+mm*60+ss
                updProgress(round(100/duration*progress), fileSize)
            else:
                #reading total video length
                p=re.match(durationRegex,line)
                if p is not None:
                    hh=float(p.group(1)) #hours
                    mm=float(p.group(2)) #mins
                    ss=float(p.group(3)) #secs (floating point, ex: 21.95)
                    duration = hh*3600+mm*60+ss
            if self.proc.poll() == 0:
                break
            elif self.proc.poll()==1:
                raise Exception(" %s<br> %s" % (slastLine, lastLine))
                break
        self.proc=None
        shutil.copymode(input_name, output_name, follow_symlinks=False)
        return fileSize

    #copy file inputPath to outputPath, calling callback every 250KB copied.
    #(250=trigger value)
    #https://hg.python.org/cpython/file/eb09f737120b/Lib/shutil.py#l215
    def copy(self, inputPath, outputPath, updProgress):
        length = 16*1024
        trigger = 250*1024
        fileSize = inputPath.stat().st_size
        copied = count = 0
        fsrc = open(inputPath, 'rb')
        fdst = open(outputPath, 'wb')
        while self.continueWork:
            buf = fsrc.read(length)
            if not buf:
                break
            fdst.write(buf)
            copied += len(buf)
            count += len(buf)
            if count >= trigger:
                count = 0
                updProgress(round(100/fileSize*copied), fileSize)
        shutil.copymode(inputPath, outputPath, follow_symlinks=False)
        return fileSize

    def getTotalSize(self, inputPath): #type (inputPath) = <class pathlib>
        #inputPath is a file:
        size = 0
        if inputPath.is_file():
            return inputPath.stat().st_size
        #inputPath is a folder:
        for item in inputPath.iterdir():
            size += self.getTotalSize(item)
        return size
