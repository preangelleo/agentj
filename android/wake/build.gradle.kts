// Pure JVM audio state machine; the app supplies the pinned sherpa native speech detector.
plugins {
    id("org.jetbrains.kotlin.jvm")
}

kotlin { jvmToolchain(17) }

dependencies {
    testImplementation("junit:junit:4.13.2")
}

tasks.test {
    // Models live in the app's assets (fetched, not committed).
    systemProperty("jarvis.models", rootProject.file("app/src/main/assets/models").absolutePath)
    testLogging { events("passed", "failed"); showStandardStreams = true }
}
