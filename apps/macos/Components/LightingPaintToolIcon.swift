import SwiftUI

struct LightingPaintToolIcon: View {
    let tool: LightingPaintTool

    var body: some View {
        Group {
            if tool == .bucket { PaintBucketShape().fill().padding(1) } else { Image(systemName: "paintbrush.pointed") }
        }.frame(width: 24, height: 24).accessibilityHidden(true)
    }
}

// A vector icon keeps the bucket available on every supported macOS version.
private struct PaintBucketShape: Shape {
    func path(in rect: CGRect) -> Path {
        let scale = min(rect.width, rect.height) / 24
        let transform = CGAffineTransform(scaleX: scale, y: scale).concatenating(
            CGAffineTransform(translationX: rect.midX - 12 * scale, y: rect.midY - 12 * scale))
        let stroke = StrokeStyle(lineWidth: 1.8, lineCap: .round, lineJoin: .round)
        var body = Path()
        body.move(to: CGPoint(x: 4, y: 9))
        body.addLine(to: CGPoint(x: 10, y: 3))
        body.addQuadCurve(to: CGPoint(x: 11.5, y: 3), control: CGPoint(x: 10.75, y: 2.25))
        body.addLine(to: CGPoint(x: 18.5, y: 10))
        body.addQuadCurve(to: CGPoint(x: 18.5, y: 12), control: CGPoint(x: 19.5, y: 11))
        body.addLine(to: CGPoint(x: 10, y: 20.5))
        body.addQuadCurve(to: CGPoint(x: 7.5, y: 20.5), control: CGPoint(x: 8.75, y: 21.75))
        body.addLine(to: CGPoint(x: 1.75, y: 14.75))
        body.addQuadCurve(to: CGPoint(x: 1.75, y: 12.25), control: CGPoint(x: 0.5, y: 13.5))
        body.addLine(to: CGPoint(x: 2.5, y: 11.5))
        var path = body.strokedPath(stroke)

        // The looping handle and its attachment remain distinct at toolbar size.
        var handle = Path()
        handle.move(to: CGPoint(x: 8.5, y: 4.5))
        handle.addCurve(to: CGPoint(x: 3.5, y: 10), control1: CGPoint(x: 1, y: 3), control2: CGPoint(x: 0.5, y: 6.5))
        handle.addCurve(to: CGPoint(x: 12, y: 10), control1: CGPoint(x: 5.5, y: 13), control2: CGPoint(x: 9, y: 12))
        path.addPath(handle.strokedPath(stroke))
        path.addEllipse(in: CGRect(x: 10.5, y: 8, width: 3.5, height: 3.5))

        // A solid stream makes the pouring action readable without fine detail.
        var paint = Path()
        paint.move(to: CGPoint(x: 17.5, y: 8.75))
        paint.addCurve(to: CGPoint(x: 23, y: 11), control1: CGPoint(x: 21, y: 7), control2: CGPoint(x: 23, y: 8))
        paint.addCurve(to: CGPoint(x: 22, y: 18), control1: CGPoint(x: 23.5, y: 13), control2: CGPoint(x: 22.75, y: 18))
        paint.addCurve(
            to: CGPoint(x: 20.75, y: 12), control1: CGPoint(x: 21.25, y: 18), control2: CGPoint(x: 21.25, y: 13))
        paint.addQuadCurve(to: CGPoint(x: 19, y: 11), control: CGPoint(x: 20.5, y: 10.75))
        paint.closeSubpath()
        path.addPath(paint)
        return path.applying(transform)
    }
}
